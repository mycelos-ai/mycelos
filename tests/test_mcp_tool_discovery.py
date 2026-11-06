"""The agent receives enough schema detail to call a live knowledge tool."""
from types import SimpleNamespace

from mycelos.tools.connector import execute_connector_tools


class DocsManager:
    def list_tools(self):
        return [{"name": "docs.search", "description": "Search documents", "input_schema": {
            "type": "object", "properties": {"filter": {"type": "object", "properties": {
                "state": {"type": "string", "enum": ["draft", "published"]}}, "required": ["state"]}},
            "required": ["filter"],
        }}]


def test_discovery_retains_nested_argument_constraints():
    result = execute_connector_tools({"connector_id": "docs"},
                                     {"app": SimpleNamespace(_mcp_manager=DocsManager())})
    schema = result["tools"][0].get("input_schema", {})
    assert schema.get("required") == ["filter"]
    assert schema["properties"]["filter"]["required"] == ["state"]
    assert schema["properties"]["filter"]["properties"]["state"]["enum"] == ["draft", "published"]
