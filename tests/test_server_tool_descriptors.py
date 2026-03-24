import pytest

import api_adapter  # noqa: F401
import server


def _iter_property_paths(schema: dict, prefix: str = ""):
    properties = schema.get("properties")
    if isinstance(properties, dict):
        for name, prop_schema in properties.items():
            current = f"{prefix}.{name}" if prefix else name
            yield current, prop_schema
            if isinstance(prop_schema, dict):
                yield from _iter_property_paths(prop_schema, current)
    definitions = schema.get("$defs")
    if isinstance(definitions, dict):
        for name, definition_schema in definitions.items():
            definition_prefix = f"$defs.{name}"
            if isinstance(definition_schema, dict):
                yield from _iter_property_paths(definition_schema, definition_prefix)


@pytest.mark.asyncio
async def test_tools_list_includes_annotations_and_custom_metadata():
    tools = await server.mcp_server.list_tools()
    by_name = {tool.name: tool.model_dump(by_alias=True) for tool in tools}

    expected = {
        "context": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
        "projects": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False},
        "tasks": {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": False},
        "knowledge": {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": False},
        "collaboration": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False},
    }

    for name, expected_annotations in expected.items():
        descriptor = by_name[name]
        annotations = descriptor["annotations"]
        assert annotations is not None
        for hint_name, hint_value in expected_annotations.items():
            assert annotations[hint_name] == hint_value

        metadata = descriptor["_meta"]
        assert metadata is not None
        for required_field in (
            "risk_level",
            "latency_class",
            "cost_class",
            "auth_required",
            "deprecated",
            "read_only",
            "idempotent",
            "annotation_defaults",
        ):
            assert required_field in metadata
        assert metadata["read_only"] == expected_annotations["readOnlyHint"]
        assert metadata["idempotent"] == expected_annotations["idempotentHint"]
        assert metadata["annotation_defaults"] == {
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
            "openWorldHint": True,
        }


@pytest.mark.asyncio
async def test_all_tool_input_schema_properties_include_descriptions():
    tools = await server.mcp_server.list_tools()
    missing: list[str] = []

    for tool in tools:
        descriptor = tool.model_dump(by_alias=True)
        input_schema = descriptor.get("inputSchema") or {}
        for path, property_schema in _iter_property_paths(input_schema):
            if not isinstance(property_schema, dict):
                continue
            description = property_schema.get("description")
            if not isinstance(description, str) or not description.strip():
                missing.append(f"{tool.name}:{path}")

    assert not missing, f"Missing parameter descriptions: {missing}"


@pytest.mark.asyncio
async def test_tools_list_includes_callable_hint():
    # Unauthenticated
    server.auth_token.set(None)
    tools = await server.mcp_server.list_tools()
    for tool in tools:
        meta = getattr(tool, "_meta", {})
        assert "callable" in meta
        if meta.get("auth_required"):
            assert not meta["callable"]
        else:
            assert meta["callable"]

    # Authenticated
    server.auth_token.set("test_token")
    tools = await server.mcp_server.list_tools()
    for tool in tools:
        meta = getattr(tool, "_meta", {})
        assert "callable" in meta
        assert meta["callable"]
