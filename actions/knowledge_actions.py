from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Protocol


def normalize_knowledge_entity(entity: str) -> str:
    normalized_entity = entity.strip().lower()
    if normalized_entity.endswith("s"):
        normalized_entity = normalized_entity[:-1]
    return normalized_entity


def validate_knowledge_entity(entity: str) -> Optional[str]:
    if entity not in {"decision", "fact", "skill"}:
        return "Error: entity must be one of: decision, fact, skill."
    return None


@dataclass(frozen=True)
class KnowledgeContext:
    api_get: Any
    api_post: Any
    api_patch: Any
    api_delete: Any
    require_fields: Any
    preview: Any


class KnowledgeEntityAdapter(Protocol):
    requires_project_for_list: bool
    requires_project_for_create: bool

    async def list_items(
        self,
        *,
        ctx: KnowledgeContext,
        project_id: Optional[str],
        category: Optional[str],
        q: Optional[str],
        cursor: Optional[str],
        limit: Optional[int],
    ) -> str: ...

    async def get_item(
        self,
        *,
        ctx: KnowledgeContext,
        item_id: Optional[str],
    ) -> str: ...

    async def create_item(
        self,
        *,
        ctx: KnowledgeContext,
        project_id: Optional[str],
        title: Optional[str],
        body: Optional[str],
        rationale: Optional[str],
        task_id: Optional[str],
        category: Optional[str],
        tags: Optional[list[str]],
    ) -> str: ...

    async def update_item(
        self,
        *,
        ctx: KnowledgeContext,
        item_id: Optional[str],
        title: Optional[str],
        body: Optional[str],
        rationale: Optional[str],
        task_id: Optional[str],
        category: Optional[str],
        tags: Optional[list[str]],
    ) -> str: ...

    async def delete_item(
        self,
        *,
        ctx: KnowledgeContext,
        item_id: Optional[str],
    ) -> str: ...


class DecisionAdapter:
    requires_project_for_list = True
    requires_project_for_create = True

    async def list_items(
        self,
        *,
        ctx: KnowledgeContext,
        project_id: Optional[str],
        category: Optional[str],
        q: Optional[str],
        cursor: Optional[str],
        limit: Optional[int],
    ) -> str:
        del category
        result = await ctx.api_get(
            f"/api/projects/{project_id}/decisions",
            params={"q": q, "cursor": cursor, "limit": limit},
        )
        items = result.get("items", [])
        if not items:
            return "No decisions found."
        lines = [f"# Decisions ({len(items)})"]
        for item in items:
            task_str = f" (task: {item['task_id']})" if item.get("task_id") else ""
            lines.append(f"- {item['title']}{task_str} (ID: {item['id']})")
            if item.get("rationale"):
                lines.append(f"  {ctx.preview(item['rationale'], 200)}")
        if result.get("next_cursor"):
            lines.append(f"\nnext_cursor: {result['next_cursor']}")
        return "\n".join(lines)

    async def get_item(self, *, ctx: KnowledgeContext, item_id: Optional[str]) -> str:
        item = await ctx.api_get(f"/api/decisions/{item_id}")
        return (
            f"# Decision: {item['title']}\n"
            f"ID: {item['id']}\n"
            f"Project: {item['project_id']}\n"
            f"Task: {item.get('task_id') or '(none)'}\n"
            f"\nRationale:\n{item.get('rationale') or '(none)'}"
        )

    async def create_item(
        self,
        *,
        ctx: KnowledgeContext,
        project_id: Optional[str],
        title: Optional[str],
        body: Optional[str],
        rationale: Optional[str],
        task_id: Optional[str],
        category: Optional[str],
        tags: Optional[list[str]],
    ) -> str:
        del body, category, tags
        payload = {"title": title, "rationale": rationale, "author_type": "agent", "task_id": task_id}
        payload = {key: value for key, value in payload.items() if value is not None}
        item = await ctx.api_post(f"/api/projects/{project_id}/decisions", body=payload)
        return f"Decision recorded: '{item['title']}' (ID: {item['id']})"

    async def update_item(
        self,
        *,
        ctx: KnowledgeContext,
        item_id: Optional[str],
        title: Optional[str],
        body: Optional[str],
        rationale: Optional[str],
        task_id: Optional[str],
        category: Optional[str],
        tags: Optional[list[str]],
    ) -> str:
        del body, category, tags
        payload = {"title": title, "rationale": rationale, "task_id": task_id}
        payload = {key: value for key, value in payload.items() if value is not None}
        if not payload:
            return "Error: action 'update' requires at least one mutable field."
        item = await ctx.api_patch(f"/api/decisions/{item_id}", body=payload)
        return f"Decision updated: '{item['title']}' (ID: {item['id']})"

    async def delete_item(self, *, ctx: KnowledgeContext, item_id: Optional[str]) -> str:
        await ctx.api_delete(f"/api/decisions/{item_id}")
        return f"Decision deleted (ID: {item_id})"


class FactAdapter:
    requires_project_for_list = True
    requires_project_for_create = True

    async def list_items(
        self,
        *,
        ctx: KnowledgeContext,
        project_id: Optional[str],
        category: Optional[str],
        q: Optional[str],
        cursor: Optional[str],
        limit: Optional[int],
    ) -> str:
        del category
        result = await ctx.api_get(
            f"/api/projects/{project_id}/facts",
            params={"q": q, "cursor": cursor, "limit": limit},
        )
        items = result.get("items", [])
        if not items:
            return "No facts found."
        lines = [f"# Facts ({len(items)})"]
        for item in items:
            category_str = f" [{item['category']}]" if item.get("category") else ""
            lines.append(f"- {item['title']}{category_str} (ID: {item['id']})")
            if item.get("body"):
                lines.append(f"  {ctx.preview(item['body'], 200)}")
        if result.get("next_cursor"):
            lines.append(f"\nnext_cursor: {result['next_cursor']}")
        return "\n".join(lines)

    async def get_item(self, *, ctx: KnowledgeContext, item_id: Optional[str]) -> str:
        del ctx, item_id
        return "Error: facts do not currently expose a dedicated GET by ID endpoint. Use action='list' with q filtering."

    async def create_item(
        self,
        *,
        ctx: KnowledgeContext,
        project_id: Optional[str],
        title: Optional[str],
        body: Optional[str],
        rationale: Optional[str],
        task_id: Optional[str],
        category: Optional[str],
        tags: Optional[list[str]],
    ) -> str:
        del rationale, task_id, tags
        payload = {"title": title, "body": body, "category": category, "author_type": "agent"}
        payload = {key: value for key, value in payload.items() if value is not None}
        item = await ctx.api_post(f"/api/projects/{project_id}/facts", body=payload)
        category_str = f" [{item['category']}]" if item.get("category") else ""
        return f"Fact recorded{category_str}: {item['title']} (ID: {item['id']})"

    async def update_item(
        self,
        *,
        ctx: KnowledgeContext,
        item_id: Optional[str],
        title: Optional[str],
        body: Optional[str],
        rationale: Optional[str],
        task_id: Optional[str],
        category: Optional[str],
        tags: Optional[list[str]],
    ) -> str:
        del rationale, task_id, tags
        payload = {"title": title, "body": body, "category": category}
        payload = {key: value for key, value in payload.items() if value is not None}
        if not payload:
            return "Error: action 'update' requires at least one mutable field."
        item = await ctx.api_patch(f"/api/facts/{item_id}", body=payload)
        return f"Fact updated: '{item['title']}' (ID: {item['id']})"

    async def delete_item(self, *, ctx: KnowledgeContext, item_id: Optional[str]) -> str:
        await ctx.api_delete(f"/api/facts/{item_id}")
        return f"Fact deleted (ID: {item_id})"


class SkillAdapter:
    requires_project_for_list = False
    requires_project_for_create = False

    async def list_items(
        self,
        *,
        ctx: KnowledgeContext,
        project_id: Optional[str],
        category: Optional[str],
        q: Optional[str],
        cursor: Optional[str],
        limit: Optional[int],
    ) -> str:
        result = await ctx.api_get(
            "/api/skills",
            params={"project_id": project_id, "category": category, "q": q, "cursor": cursor, "limit": limit},
        )
        items = result.get("items", [])
        if not items:
            return "No skills found."
        lines = [f"# Skills ({len(items)})"]
        for item in items:
            scope = "team-wide" if not item.get("project_id") else "project"
            category_str = f" [{item['category']}]" if item.get("category") else ""
            tags_str = f" tags:{','.join(item['tags'])}" if item.get("tags") else ""
            lines.append(f"- {item['title']}{category_str}{tags_str} ({scope}) (ID: {item['id']})")
            if item.get("body"):
                lines.append(f"  {ctx.preview(item['body'], 200)}")
        if result.get("next_cursor"):
            lines.append(f"\nnext_cursor: {result['next_cursor']}")
        return "\n".join(lines)

    async def get_item(self, *, ctx: KnowledgeContext, item_id: Optional[str]) -> str:
        item = await ctx.api_get(f"/api/skills/{item_id}")
        scope = f"project:{item['project_id']}" if item.get("project_id") else "team-wide"
        category_str = f"Category: {item['category']}\n" if item.get("category") else ""
        tags_str = f"Tags: {', '.join(item['tags'])}\n" if item.get("tags") else ""
        return (
            f"# {item['title']}\n"
            f"ID: {item['id']}\n"
            f"Scope: {scope}\n"
            f"{category_str}{tags_str}"
            f"Author: {item['author_type']} ({item['author_id']})\n"
            f"\n{item['body']}"
        )

    async def create_item(
        self,
        *,
        ctx: KnowledgeContext,
        project_id: Optional[str],
        title: Optional[str],
        body: Optional[str],
        rationale: Optional[str],
        task_id: Optional[str],
        category: Optional[str],
        tags: Optional[list[str]],
    ) -> str:
        del rationale, task_id
        payload = {"title": title, "body": body, "category": category, "tags": tags, "author_type": "agent"}
        payload = {key: value for key, value in payload.items() if value is not None}
        if project_id:
            item = await ctx.api_post(f"/api/projects/{project_id}/skills", body=payload)
        else:
            item = await ctx.api_post("/api/skills", body=payload)
        scope = f"project {project_id}" if project_id else "team-wide"
        category_str = f" [{item['category']}]" if item.get("category") else ""
        return f"Skill published{category_str}: '{item['title']}' ({scope}) (ID: {item['id']})"

    async def update_item(
        self,
        *,
        ctx: KnowledgeContext,
        item_id: Optional[str],
        title: Optional[str],
        body: Optional[str],
        rationale: Optional[str],
        task_id: Optional[str],
        category: Optional[str],
        tags: Optional[list[str]],
    ) -> str:
        del rationale, task_id
        payload = {"title": title, "body": body, "category": category, "tags": tags}
        payload = {key: value for key, value in payload.items() if value is not None}
        if not payload:
            return "Error: action 'update' requires at least one mutable field."
        item = await ctx.api_patch(f"/api/skills/{item_id}", body=payload)
        return f"Skill updated: '{item['title']}' (ID: {item['id']})"

    async def delete_item(self, *, ctx: KnowledgeContext, item_id: Optional[str]) -> str:
        await ctx.api_delete(f"/api/skills/{item_id}")
        return f"Skill deleted (ID: {item_id})"


KNOWLEDGE_ENTITY_ADAPTERS: dict[str, KnowledgeEntityAdapter] = {
    "decision": DecisionAdapter(),
    "fact": FactAdapter(),
    "skill": SkillAdapter(),
}


def _adapter_for_entity(entity: str) -> KnowledgeEntityAdapter:
    return KNOWLEDGE_ENTITY_ADAPTERS[entity]


def _build_ctx(*, api_get: Any, api_post: Any, api_patch: Any, api_delete: Any, require_fields: Any, preview: Any) -> KnowledgeContext:
    return KnowledgeContext(
        api_get=api_get,
        api_post=api_post,
        api_patch=api_patch,
        api_delete=api_delete,
        require_fields=require_fields,
        preview=preview,
    )


async def _knowledge_list_via_adapter(
    *,
    adapter: KnowledgeEntityAdapter,
    ctx: KnowledgeContext,
    project_id: Optional[str],
    category: Optional[str],
    q: Optional[str],
    cursor: Optional[str],
    limit: Optional[int],
) -> str:
    if adapter.requires_project_for_list:
        error = ctx.require_fields("list", project_id=project_id)
        if error:
            return error
    return await adapter.list_items(
        ctx=ctx,
        project_id=project_id,
        category=category,
        q=q,
        cursor=cursor,
        limit=limit,
    )


async def _knowledge_get_via_adapter(*, adapter: KnowledgeEntityAdapter, ctx: KnowledgeContext, item_id: Optional[str]) -> str:
    error = ctx.require_fields("get", item_id=item_id)
    if error:
        return error
    return await adapter.get_item(ctx=ctx, item_id=item_id)


async def _knowledge_create_via_adapter(
    *,
    adapter: KnowledgeEntityAdapter,
    ctx: KnowledgeContext,
    project_id: Optional[str],
    title: Optional[str],
    body: Optional[str],
    rationale: Optional[str],
    task_id: Optional[str],
    category: Optional[str],
    tags: Optional[list[str]],
) -> str:
    if adapter.requires_project_for_create:
        error = ctx.require_fields("create", project_id=project_id, title=title)
    else:
        error = ctx.require_fields("create", title=title, body=body)
    if error:
        return error
    return await adapter.create_item(
        ctx=ctx,
        project_id=project_id,
        title=title,
        body=body,
        rationale=rationale,
        task_id=task_id,
        category=category,
        tags=tags,
    )


async def _knowledge_update_via_adapter(
    *,
    adapter: KnowledgeEntityAdapter,
    ctx: KnowledgeContext,
    item_id: Optional[str],
    title: Optional[str],
    body: Optional[str],
    rationale: Optional[str],
    task_id: Optional[str],
    category: Optional[str],
    tags: Optional[list[str]],
) -> str:
    error = ctx.require_fields("update", item_id=item_id)
    if error:
        return error
    return await adapter.update_item(
        ctx=ctx,
        item_id=item_id,
        title=title,
        body=body,
        rationale=rationale,
        task_id=task_id,
        category=category,
        tags=tags,
    )


async def _knowledge_delete_via_adapter(*, adapter: KnowledgeEntityAdapter, ctx: KnowledgeContext, item_id: Optional[str]) -> str:
    error = ctx.require_fields("delete", item_id=item_id)
    if error:
        return error
    return await adapter.delete_item(ctx=ctx, item_id=item_id)


async def knowledge_action_list(
    *,
    api_get: Any,
    require_fields: Any,
    preview: Any,
    entity: str,
    project_id: Optional[str],
    category: Optional[str],
    q: Optional[str],
    cursor: Optional[str],
    limit: Optional[int],
    **_: Any,
) -> str:
    adapter = _adapter_for_entity(entity)
    ctx = _build_ctx(
        api_get=api_get,
        api_post=None,
        api_patch=None,
        api_delete=None,
        require_fields=require_fields,
        preview=preview,
    )
    return await _knowledge_list_via_adapter(
        adapter=adapter,
        ctx=ctx,
        project_id=project_id,
        category=category,
        q=q,
        cursor=cursor,
        limit=limit,
    )


async def knowledge_action_get(
    *,
    api_get: Any,
    require_fields: Any,
    preview: Any,
    entity: str,
    item_id: Optional[str],
    **_: Any,
) -> str:
    adapter = _adapter_for_entity(entity)
    ctx = _build_ctx(
        api_get=api_get,
        api_post=None,
        api_patch=None,
        api_delete=None,
        require_fields=require_fields,
        preview=preview,
    )
    return await _knowledge_get_via_adapter(adapter=adapter, ctx=ctx, item_id=item_id)


async def knowledge_action_create(
    *,
    api_get: Any,
    api_post: Any,
    require_fields: Any,
    preview: Any,
    entity: str,
    project_id: Optional[str],
    title: Optional[str],
    body: Optional[str],
    rationale: Optional[str],
    task_id: Optional[str],
    category: Optional[str],
    tags: Optional[list[str]],
    **_: Any,
) -> str:
    adapter = _adapter_for_entity(entity)
    ctx = _build_ctx(
        api_get=api_get,
        api_post=api_post,
        api_patch=None,
        api_delete=None,
        require_fields=require_fields,
        preview=preview,
    )
    return await _knowledge_create_via_adapter(
        adapter=adapter,
        ctx=ctx,
        project_id=project_id,
        title=title,
        body=body,
        rationale=rationale,
        task_id=task_id,
        category=category,
        tags=tags,
    )


async def knowledge_action_update(
    *,
    api_get: Any,
    api_patch: Any,
    require_fields: Any,
    preview: Any,
    entity: str,
    item_id: Optional[str],
    title: Optional[str],
    body: Optional[str],
    rationale: Optional[str],
    task_id: Optional[str],
    category: Optional[str],
    tags: Optional[list[str]],
    **_: Any,
) -> str:
    adapter = _adapter_for_entity(entity)
    ctx = _build_ctx(
        api_get=api_get,
        api_post=None,
        api_patch=api_patch,
        api_delete=None,
        require_fields=require_fields,
        preview=preview,
    )
    return await _knowledge_update_via_adapter(
        adapter=adapter,
        ctx=ctx,
        item_id=item_id,
        title=title,
        body=body,
        rationale=rationale,
        task_id=task_id,
        category=category,
        tags=tags,
    )


async def knowledge_action_delete(
    *,
    api_get: Any,
    api_delete: Any,
    require_fields: Any,
    preview: Any,
    entity: str,
    item_id: Optional[str],
    **_: Any,
) -> str:
    adapter = _adapter_for_entity(entity)
    ctx = _build_ctx(
        api_get=api_get,
        api_post=None,
        api_patch=None,
        api_delete=api_delete,
        require_fields=require_fields,
        preview=preview,
    )
    return await _knowledge_delete_via_adapter(adapter=adapter, ctx=ctx, item_id=item_id)


KNOWLEDGE_ACTION_HANDLERS = {
    "list": knowledge_action_list,
    "get": knowledge_action_get,
    "create": knowledge_action_create,
    "update": knowledge_action_update,
    "delete": knowledge_action_delete,
}
