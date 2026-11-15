"""Test40：规范工具定义及定义校验。"""

from __future__ import annotations

from typing import Any


query_order_status = {
    "type": "function",
    "function": {
        "name": "query_order_status",
        "description": (
            "用途说明：查询指定订单当前的业务处理状态及可选历史状态；"
            "使用时机：用户提供有效订单号并询问订单是否已确认、处理或完成时使用；"
            "限制条件：仅查询订单业务状态，不提供包裹位置，物流轨迹查询请改用 query_logistics。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "order_id": {
                    "type": "string",
                    "description": "订单编号，必须以 SO- 开头，例如 SO-1001。",
                    "pattern": "^SO-",
                },
                "include_history": {
                    "type": "boolean",
                    "description": "是否同时返回历史状态记录，默认不返回。",
                    "default": False,
                },
            },
            "required": ["order_id"],
            "additionalProperties": False,
        },
    },
}


def validate_tool_def(tool_def: dict[str, Any]) -> list[str]:
    """返回工具定义中的全部错误；空列表表示校验通过。"""
    errors: list[str] = []
    definition = tool_def.get("function", tool_def)

    name = definition.get("name")
    if not isinstance(name, str) or not name.strip():
        errors.append("name 必须是非空字符串")

    description = definition.get("description")
    if not isinstance(description, str) or len(description.strip()) < 20:
        errors.append("description 长度不得低于 20 个字符")

    parameters = definition.get("parameters")
    if not isinstance(parameters, dict):
        errors.append("parameters 必须是 JSON Schema 对象")
        return errors

    properties = parameters.get("properties", {})
    required = parameters.get("required", [])
    if not isinstance(properties, dict):
        errors.append("parameters.properties 必须是对象")
        properties = {}
    if not isinstance(required, list):
        errors.append("parameters.required 必须是数组")
        required = []

    for parameter_name in required:
        schema = properties.get(parameter_name)
        if not isinstance(schema, dict):
            errors.append(f"必填参数 {parameter_name} 未在 properties 中定义")
            continue
        if not schema.get("type"):
            errors.append(f"必填参数 {parameter_name} 缺少 type")
        if not isinstance(schema.get("description"), str) or not schema["description"].strip():
            errors.append(f"必填参数 {parameter_name} 缺少 description")

    return errors


if __name__ == "__main__":
    print("规范定义校验结果：", validate_tool_def(query_order_status) or "通过")

    invalid_definition = {
        "name": "",
        "description": "太短",
        "parameters": {
            "type": "object",
            "properties": {"order_id": {}},
            "required": ["order_id"],
        },
    }
    print("错误定义校验结果：")
    for error in validate_tool_def(invalid_definition):
        print("-", error)
