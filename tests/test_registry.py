"""
工具注册表测试
"""
import json
import pytest
from hermes.tools.registry import ToolRegistry, tool_result, tool_error


def test_tool_result():
    """测试 tool_result 函数"""
    result = tool_result(foo="bar", value=42)
    data = json.loads(result)
    assert data["foo"] == "bar"
    assert data["value"] == 42


def test_tool_error():
    """测试 tool_error 函数"""
    result = tool_error("something went wrong")
    data = json.loads(result)
    assert data["error"] == "something went wrong"


def test_registry_register_and_get():
    """测试工具注册与获取"""
    registry = ToolRegistry()

    def test_handler(args, **kwargs):
        return tool_result(hello="world")

    def check_fn():
        return True

    schema = {
        "name": "test_tool",
        "description": "Test tool",
        "parameters": {"type": "object", "properties": {}}
    }

    registry.register(
        name="test_tool",
        schema=schema,
        handler=test_handler,
        check_fn=check_fn,
        toolset="test",
        emoji="🧪"
    )

    assert registry.has("test_tool")
    tool = registry.get("test_tool")
    assert tool["name"] == "test_tool"
    assert tool["toolset"] == "test"
    assert tool["emoji"] == "🧪"


def test_registry_dispatch():
    """测试工具分发"""
    registry = ToolRegistry()

    def test_handler(args, **kwargs):
        return tool_result(foo=args.get("bar"))

    def check_fn():
        return True

    registry.register(
        name="test_tool",
        schema={"name": "test_tool", "parameters": {}},
        handler=test_handler,
        check_fn=check_fn
    )

    result = registry.dispatch("test_tool", {"bar": "baz"})
    data = json.loads(result)
    assert data["foo"] == "baz"


def test_registry_list_schemas():
    """测试列出所有工具 schema"""
    registry = ToolRegistry()

    def dummy_handler(args, **kwargs):
        return tool_result()

    def check_fn():
        return True

    registry.register(
        name="tool1",
        schema={"name": "tool1"},
        handler=dummy_handler,
        check_fn=check_fn
    )

    registry.register(
        name="tool2",
        schema={"name": "tool2"},
        handler=dummy_handler,
        check_fn=check_fn
    )

    schemas = registry.list_schemas()
    assert len(schemas) == 2


def test_registry_list_schemas_by_toolset():
    """测试按 toolset 过滤 schema"""
    registry = ToolRegistry()

    def dummy_handler(args, **kwargs):
        return tool_result()

    def check_fn():
        return True

    registry.register(
        name="tool1",
        schema={"name": "tool1"},
        handler=dummy_handler,
        check_fn=check_fn,
        toolset="a"
    )

    registry.register(
        name="tool2",
        schema={"name": "tool2"},
        handler=dummy_handler,
        check_fn=check_fn,
        toolset="b"
    )

    schemas_a = registry.list_schemas_by_toolset("a")
    assert len(schemas_a) == 1
