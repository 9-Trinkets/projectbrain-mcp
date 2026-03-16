#!/usr/bin/env python3
"""
Collaborator cron runner MVP.

Polls the Project Brain API on a configurable tick interval, processes
delegated tasks from the inbox, self-picks eligible tasks from the workflow,
and invokes a configurable work hook for each claimed task.

Configuration (environment variables):
  SERVER_URL       API base URL (default: http://localhost:8000)
  API_TOKEN        Bearer token for authentication (required)
  PROJECT_ID       Project UUID to operate on (required)
  AGENT_ID         This runner's agent user ID (required)
  TICK_INTERVAL    Seconds between ticks (default: 60)
  MAX_CONCURRENT   Max tasks to hold simultaneously (default: 1)
  WORK_HOOK        Shell command invoked per claimed task (optional).
                   Receives task context via env vars:
                     TASK_ID, TASK_TITLE, TASK_STATUS, PROJECT_ID
                   Exit codes: 0 = success, 1 = transient failure, 2 = permanent failure
  MAX_ATTEMPTS     Retry limit before escalating to blocked (default: 3)
  DEDUPE_MAX_SIZE  Max IDs held in the idempotency store (default: 10000)
  DEDUPE_TTL       Seconds before an ID is evicted from the store (default: 86400)
  LOG_LEVEL        Logging level (default: INFO)
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

SERVER_URL = os.getenv("SERVER_URL", "http://localhost:8000").rstrip("/")
API_TOKEN = os.getenv("API_TOKEN", "")
PROJECT_ID = os.getenv("PROJECT_ID", "")
AGENT_ID = os.getenv("AGENT_ID", "")
TICK_INTERVAL = float(os.getenv("TICK_INTERVAL", "60"))
MAX_CONCURRENT = int(os.getenv("MAX_CONCURRENT", "1"))
MAX_ATTEMPTS = int(os.getenv("MAX_ATTEMPTS", "3"))
WORK_HOOK = os.getenv("WORK_HOOK", "")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("runner")


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class Stage:
    id: str
    name: str
    statuses: list[str]       # ordered; first = entry (claimable)
    eligible_agents: list[str]

    @property
    def entry_status(self) -> str:
        return self.statuses[0]


@dataclass
class Workflow:
    statuses: set[str]
    stages: dict[str, Stage]  # stage_id → Stage


@dataclass
class ClaimedTask:
    id: str
    title: str
    status: str
    attempt: int = 1



# ---------------------------------------------------------------------------
# Idempotency store
# ---------------------------------------------------------------------------
# Maximum number of IDs to track; oldest entries are evicted when exceeded.
_DEDUPE_MAX_SIZE = int(os.getenv("DEDUPE_MAX_SIZE", "10000"))
# How long (seconds) to remember a processed ID before evicting.
_DEDUPE_TTL = float(os.getenv("DEDUPE_TTL", str(24 * 3600)))  # 24 h default


class DedupeStore:
    """Bounded, TTL-based deduplication store.

    Stores (id → recorded_at_timestamp). Evicts entries older than TTL or
    when the store exceeds max_size (oldest first).
    """

    def __init__(self, max_size: int = _DEDUPE_MAX_SIZE, ttl: float = _DEDUPE_TTL) -> None:
        self._store: dict[str, float] = {}  # id → timestamp
        self._max_size = max_size
        self._ttl = ttl

    def seen(self, key: str) -> bool:
        now = time.monotonic()
        ts = self._store.get(key)
        if ts is None:
            return False
        if now - ts > self._ttl:
            del self._store[key]
            return False
        return True

    def record(self, key: str) -> None:
        now = time.monotonic()
        self._store[key] = now
        # Evict if over capacity: remove oldest entries first
        if len(self._store) > self._max_size:
            oldest = sorted(self._store, key=lambda k: self._store[k])
            for k in oldest[: len(self._store) - self._max_size]:
                del self._store[k]

    def seen_and_record(self, key: str) -> bool:
        """Return True if already seen (duplicate). Records if new."""
        if self.seen(key):
            return True
        self.record(key)
        return False


@dataclass
class RunnerState:
    claimed_tasks: list[ClaimedTask] = field(default_factory=list)
    # run_ids persist across ticks (TTL-bounded) to deduplicate retried delegations
    run_id_store: DedupeStore = field(default_factory=DedupeStore)
    # message_ids persist across ticks so a crash-before-mark-read doesn't reprocess
    message_id_store: DedupeStore = field(default_factory=DedupeStore)
    cached_workflow: Workflow | None = None
    cache_age_ticks: int = 0   # ticks since last successful discovery


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

async def _request(
    client: httpx.AsyncClient,
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
) -> Any:
    headers = {"Authorization": f"Bearer {API_TOKEN}"}
    url = f"{SERVER_URL}{path}"
    response = await client.request(
        method, url, headers=headers, params=params, json=json_body, timeout=30.0
    )
    if response.status_code >= 400:
        try:
            detail = response.json()
            detail = detail.get("detail") or detail.get("message") or str(detail)
        except Exception:
            detail = response.text.strip() or f"HTTP {response.status_code}"
        raise ApiError(response.status_code, detail, _try_conflict_code(response))
    if response.status_code == 204 or not response.content:
        return None
    return response.json()


def _try_conflict_code(response: httpx.Response) -> str | None:
    try:
        body = response.json()
        # FastAPI wraps detail as a string or dict
        detail = body.get("detail", body)
        if isinstance(detail, dict):
            return detail.get("conflict_code")
        return None
    except Exception:
        return None


class ApiError(Exception):
    def __init__(self, status: int, detail: str, conflict_code: str | None = None):
        super().__init__(detail)
        self.status = status
        self.conflict_code = conflict_code


# ---------------------------------------------------------------------------
# Workflow discovery
# ---------------------------------------------------------------------------

async def discover_workflow(client: httpx.AsyncClient) -> Workflow | None:
    try:
        data = await _request(client, "GET", f"/api/projects/{PROJECT_ID}/workflow")
    except Exception as exc:
        log.warning("Workflow discovery failed: %s", exc)
        return None

    stages: dict[str, Stage] = {}
    for s in data.get("stages", []):
        stage_statuses = [st["name"] for st in s.get("statuses", [])]
        if not stage_statuses:
            continue
        stages[s["id"]] = Stage(
            id=s["id"],
            name=s["name"],
            statuses=stage_statuses,
            eligible_agents=[a["id"] for a in s.get("claimed_agents", [])],
        )

    all_statuses = {st["name"] for st in data.get("statuses", [])}
    return Workflow(statuses=all_statuses, stages=stages)


# ---------------------------------------------------------------------------
# CAS claim
# ---------------------------------------------------------------------------

@dataclass
class ClaimResult:
    ok: bool
    task: dict[str, Any] | None = None
    conflict_code: str | None = None
    trigger_rediscovery: bool = False


async def attempt_cas_claim(
    client: httpx.AsyncClient,
    state: RunnerState,
    task_id: str,
    expected_status: str | None,
    to_status: str,
    run_id: str,
) -> ClaimResult:
    """Best-effort claim: pre-check status then assign.

    The API does not yet have an atomic CAS endpoint (task a5521314). We
    approximate it with a fetch-then-patch: verify the task is still in
    expected_status before writing. A race between two runners is possible
    but will surface as a status mismatch on the next tick.
    """
    if state.run_id_store.seen_and_record(run_id):
        log.debug("Skipping duplicate run_id %s", run_id)
        return ClaimResult(ok=False, conflict_code="run_id_seen")

    # Pre-check: fetch task to verify status and grab updated_at for the version guard
    try:
        current = await _request(client, "GET", f"/api/tasks/{task_id}")
    except ApiError as exc:
        if exc.status == 404:
            return ClaimResult(ok=False, conflict_code="not_found")
        raise

    if expected_status and current.get("status") != expected_status:
        return ClaimResult(ok=False, conflict_code="status_mismatch")

    # If already assigned to someone else, skip
    current_assignee = current.get("assignee_id")
    if current_assignee and current_assignee != AGENT_ID:
        return ClaimResult(ok=False, conflict_code="already_claimed")

    # Build PATCH with both status guard and version guard for strong exclusivity
    patch: dict[str, Any] = {"status": to_status, "assignee_id": AGENT_ID}
    if expected_status:
        patch["expected_status"] = expected_status
    if current.get("updated_at"):
        patch["expected_updated_at"] = current["updated_at"]

    try:
        task = await _request(client, "PATCH", f"/api/tasks/{task_id}", json_body=patch)
        return ClaimResult(ok=True, task=task)
    except ApiError as exc:
        if exc.status == 409:
            code = exc.conflict_code or "status_mismatch"
            # version_mismatch means another runner claimed between our read and write
            return ClaimResult(ok=False, conflict_code=code)
        if exc.status == 404:
            return ClaimResult(ok=False, conflict_code="not_found")
        if exc.status == 400:
            # Likely invalid status for this project — workflow may have changed
            return ClaimResult(ok=False, conflict_code="workflow_changed", trigger_rediscovery=True)
        raise


# ---------------------------------------------------------------------------
# Inbox processing
# ---------------------------------------------------------------------------

async def process_inbox(
    client: httpx.AsyncClient,
    state: RunnerState,
    workflow: Workflow,
) -> None:
    try:
        messages = await _request(client, "GET", "/api/a2a/messages", params={"unread_only": True})
    except Exception as exc:
        log.warning("Failed to fetch inbox: %s", exc)
        return

    for msg in (messages or []):
        msg_id = msg.get("id", "")
        if state.message_id_store.seen_and_record(msg_id):
            # Already processed in a prior tick; mark read defensively and skip
            log.debug("Skipping duplicate message %s", msg_id)
            try:
                await _request(client, "PATCH", f"/api/a2a/messages/{msg_id}/read")
            except Exception:
                pass
            continue
        try:
            await _handle_message(client, state, workflow, msg)
        except Exception as exc:
            log.error("Error handling message %s: %s", msg_id, exc)
        finally:
            try:
                await _request(client, "PATCH", f"/api/a2a/messages/{msg_id}/read")
            except Exception as exc:
                log.warning("Failed to mark message %s read: %s", msg_id, exc)


async def _handle_message(
    client: httpx.AsyncClient,
    state: RunnerState,
    workflow: Workflow,
    msg: dict[str, Any],
) -> None:
    mtype = msg.get("message_type")
    meta = msg.get("metadata") or {}

    if mtype == "task_delegation":
        task_id = meta.get("task_id")
        if not task_id:
            log.warning("task_delegation missing task_id, skipping")
            return

        expected_status = meta.get("expected_status")
        to_status = meta.get("to_status")
        run_id = meta.get("run_id") or str(uuid.uuid4())

        if not to_status:
            # Infer entry status for this agent from workflow
            to_status = _entry_status_for_agent(workflow)
        if not to_status:
            log.warning("Cannot determine to_status for task %s, skipping", task_id)
            return

        if len(state.claimed_tasks) >= MAX_CONCURRENT:
            log.info("At capacity (%d), skipping delegated task %s", MAX_CONCURRENT, task_id)
            return

        result = await attempt_cas_claim(client, state, task_id, expected_status, to_status, run_id)
        if result.ok and result.task:
            ct = ClaimedTask(id=task_id, title=result.task.get("title", ""), status=to_status)
            state.claimed_tasks.append(ct)
            log.info("Claimed delegated task %s (%s)", task_id, ct.title)
        else:
            log.info("Delegation claim rejected for %s: %s", task_id, result.conflict_code)

    elif mtype == "question":
        log.info("Received question (ID: %s); escalating — no automated answer.", msg.get("id"))

    else:
        log.debug("Ignoring message type '%s' (ID: %s)", mtype, msg.get("id"))


def _entry_status_for_agent(workflow: Workflow) -> str | None:
    for stage in workflow.stages.values():
        if AGENT_ID in stage.eligible_agents:
            return stage.entry_status
    return None


# ---------------------------------------------------------------------------
# Self-pick
# ---------------------------------------------------------------------------

async def self_pick(
    client: httpx.AsyncClient,
    state: RunnerState,
    workflow: Workflow,
) -> None:
    if len(state.claimed_tasks) >= MAX_CONCURRENT:
        return

    for stage in workflow.stages.values():
        if AGENT_ID not in stage.eligible_agents:
            continue

        entry = stage.entry_status
        try:
            # Fetch tasks at entry status; filter unassigned client-side
            # (the API has no "unassigned" sentinel — assignee_id accepts UUID only)
            page = await _request(
                client, "GET", f"/api/projects/{PROJECT_ID}/tasks",
                params={"status": entry, "limit": 20},
            )
        except Exception as exc:
            log.warning("Failed to list candidates for stage %s: %s", stage.name, exc)
            continue

        candidates = [t for t in (page or {}).get("items", []) if not t.get("assignee_id")]
        for task in candidates:
            if len(state.claimed_tasks) >= MAX_CONCURRENT:
                return
            run_id = str(uuid.uuid4())
            result = await attempt_cas_claim(
                client, state, task["id"], entry, entry, run_id
            )
            if result.ok and result.task:
                ct = ClaimedTask(id=task["id"], title=task.get("title", ""), status=entry)
                state.claimed_tasks.append(ct)
                log.info("Self-picked task %s (%s) in stage '%s'", task["id"], ct.title, stage.name)
            elif result.trigger_rediscovery:
                log.info("Workflow changed mid-tick; aborting self-pick for this tick")
                return


# ---------------------------------------------------------------------------
# Work execution
# ---------------------------------------------------------------------------

async def work_claimed_tasks(
    client: httpx.AsyncClient,
    state: RunnerState,
    workflow: Workflow,
) -> None:
    still_active: list[ClaimedTask] = []

    for ct in state.claimed_tasks:
        done = await _work_one_task(client, state, workflow, ct)
        if not done:
            still_active.append(ct)

    state.claimed_tasks = still_active


async def _work_one_task(
    client: httpx.AsyncClient,
    state: RunnerState,
    workflow: Workflow,
    ct: ClaimedTask,
) -> bool:
    """Execute work for a task. Returns True if task is finished (success or terminal failure)."""
    if not WORK_HOOK:
        log.info("No WORK_HOOK set; task %s (%s) held until hook is configured.", ct.id, ct.title)
        return False

    log.info("Running WORK_HOOK for task %s (%s), attempt %d/%d", ct.id, ct.title, ct.attempt, MAX_ATTEMPTS)

    env = {
        **os.environ,
        "TASK_ID": ct.id,
        "TASK_TITLE": ct.title,
        "TASK_STATUS": ct.status,
        "PROJECT_ID": PROJECT_ID,
        "ATTEMPT": str(ct.attempt),
    }

    try:
        proc = await asyncio.create_subprocess_shell(
            WORK_HOOK,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        exit_code = proc.returncode
    except Exception as exc:
        log.error("WORK_HOOK subprocess error for task %s: %s", ct.id, exc)
        exit_code = 1  # treat as transient

    if exit_code == 0:
        # Success: advance status and report. If the advance fails, treat as transient.
        next_status = _next_status_in_workflow(workflow, ct.status)
        try:
            await _report_outcome(client, ct, "success", next_status)
        except Exception as exc:
            log.warning("Status advance failed for task %s: %s; will retry next tick", ct.id, exc)
            ct.attempt += 1
            return False
        return True

    if exit_code == 2:
        # Permanent failure
        log.error("Task %s failed permanently (exit 2)", ct.id)
        await _escalate(client, ct, f"Permanent failure after {ct.attempt} attempt(s).")
        return True

    # Transient failure (exit 1 or other)
    ct.attempt += 1
    if ct.attempt > MAX_ATTEMPTS:
        log.warning("Task %s exceeded max attempts (%d)", ct.id, MAX_ATTEMPTS)
        await _escalate(client, ct, f"Max attempts ({MAX_ATTEMPTS}) exceeded.")
        return True

    log.info("Task %s attempt %d failed transiently; will retry next tick.", ct.id, ct.attempt)
    return False


def _next_status_in_workflow(workflow: Workflow, current_status: str) -> str | None:
    """Return the next status after current in the same stage, or None if terminal."""
    for stage in workflow.stages.values():
        if current_status in stage.statuses:
            idx = stage.statuses.index(current_status)
            if idx + 1 < len(stage.statuses):
                return stage.statuses[idx + 1]
            return None  # last in stage = terminal for this stage
    return None


async def _report_outcome(
    client: httpx.AsyncClient,
    ct: ClaimedTask,
    outcome: str,
    next_status: str | None,
) -> None:
    # Advance task status if there's a next status
    if next_status:
        await _request(client, "PATCH", f"/api/tasks/{ct.id}", json_body={"status": next_status})
        log.info("Task %s advanced to status '%s'", ct.id, next_status)

    # Post status_update message to team channel (best-effort; don't raise)
    try:
        await _request(client, "POST", "/api/a2a/chat", json_body={
            "message_type": "status_update",
            "body": f"Task '{ct.title}' completed with outcome: {outcome}.",
            "metadata": {
                "task_id": ct.id,
                "project_id": PROJECT_ID,
                "outcome": outcome,
                "attempt": ct.attempt,
            },
        })
    except Exception as exc:
        log.warning("Failed to post status_update for task %s: %s", ct.id, exc)


async def _escalate(client: httpx.AsyncClient, ct: ClaimedTask, error: str) -> None:
    # Set task to blocked
    try:
        await _request(client, "PATCH", f"/api/tasks/{ct.id}", json_body={"status": "blocked"})
    except Exception as exc:
        log.warning("Failed to set task %s to blocked: %s", ct.id, exc)

    # Post escalation question to team channel
    try:
        await _request(client, "POST", "/api/a2a/chat", json_body={
            "message_type": "question",
            "body": f"Task '{ct.title}' needs human intervention: {error}",
            "metadata": {
                "task_id": ct.id,
                "project_id": PROJECT_ID,
                "outcome": "failure",
                "error": error,
                "attempt": ct.attempt,
            },
        })
        log.warning("Escalated task %s to human: %s", ct.id, error)
    except Exception as exc:
        log.warning("Failed to send escalation for task %s: %s", ct.id, exc)


# ---------------------------------------------------------------------------
# Tick loop
# ---------------------------------------------------------------------------

async def tick(client: httpx.AsyncClient, state: RunnerState) -> None:
    log.debug("Tick started")

    # Step 1: workflow discovery — always first
    workflow = await discover_workflow(client)
    if workflow is None:
        if state.cached_workflow is not None and state.cache_age_ticks <= 1:
            log.warning("Using cached workflow (age: %d tick(s))", state.cache_age_ticks)
            workflow = state.cached_workflow
            state.cache_age_ticks += 1
        else:
            log.warning("No workflow available; skipping tick")
            return
    else:
        state.cached_workflow = workflow
        state.cache_age_ticks = 0

    # Step 2: inbox-first
    await process_inbox(client, state, workflow)

    # Step 3: self-pick
    await self_pick(client, state, workflow)

    # Step 4: work claimed tasks
    await work_claimed_tasks(client, state, workflow)

    log.debug("Tick complete. Holding %d task(s).", len(state.claimed_tasks))


async def run() -> None:
    if not API_TOKEN:
        raise SystemExit("API_TOKEN is required")
    if not PROJECT_ID:
        raise SystemExit("PROJECT_ID is required")
    if not AGENT_ID:
        raise SystemExit("AGENT_ID is required")

    log.info(
        "Runner starting — project=%s agent=%s interval=%.0fs max_concurrent=%d",
        PROJECT_ID, AGENT_ID, TICK_INTERVAL, MAX_CONCURRENT,
    )

    state = RunnerState()
    async with httpx.AsyncClient() as client:
        while True:
            try:
                await tick(client, state)
            except Exception as exc:
                log.error("Unhandled error in tick: %s", exc, exc_info=True)
            await asyncio.sleep(TICK_INTERVAL)


if __name__ == "__main__":
    asyncio.run(run())
