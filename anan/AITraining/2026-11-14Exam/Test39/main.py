"""Test39：带最大轮次兜底的最小 Agent 循环。"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, Callable


# OpenAI 兼容客户端和工具路由表可在运行前替换，因此也便于单元测试 mock。
client: Any = None
dispatch: dict[str, Callable[..., Any]] = {}


def _assistant_message_to_dict(message: Any) -> dict[str, Any]:
    """把 SDK 消息对象转换成可再次发送给模型的普通字典。"""
    data: dict[str, Any] = {
        "role": "assistant",
        "content": getattr(message, "content", None),
    }
    calls = getattr(message, "tool_calls", None)
    if calls:
        data["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.function.name,
                    "arguments": call.function.arguments,
                },
            }
            for call in calls
        ]
    return data


def run_agent(system_prompt: str, task: str, tools: list[dict[str, Any]], max_turns: int = 5) -> str:
    """运行 Agent；client 与 dispatch 均可替换为真实实现或 mock。"""
    if client is None:
        raise RuntimeError("请先配置 OpenAI 兼容 client")
    if max_turns < 1:
        return "已达最大轮次，任务未完成"

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": task},
    ]

    for _ in range(max_turns):
        response = client.chat.completions.create(
            model="mock-model",
            messages=messages,  # 每轮均发送 system 与完整历史
            tools=tools,
        )
        message = response.choices[0].message
        messages.append(_assistant_message_to_dict(message))

        tool_calls = getattr(message, "tool_calls", None) or []
        if not tool_calls:
            return message.content or ""

        for call in tool_calls:
            try:
                arguments = json.loads(call.function.arguments or "{}")
                tool_fn = dispatch[call.function.name]
                result = tool_fn(**arguments)
                payload = {"ok": True, "result": result}
            except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                payload = {"ok": False, "error": str(exc)}

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": json.dumps(payload, ensure_ascii=False),
                }
            )

    return "已达最大轮次，任务未完成"


class MockCompletions:
    """第一次要求查工具，第二次读取工具结果并给最终回答。"""

    def __init__(self) -> None:
        self.call_count = 0

    def create(self, **kwargs: Any) -> Any:
        self.call_count += 1
        print(f"模型第 {self.call_count} 次收到 {len(kwargs['messages'])} 条完整历史消息")
        if self.call_count == 1:
            tool_call = SimpleNamespace(
                id="call_001",
                function=SimpleNamespace(
                    name="query_weather",
                    arguments=json.dumps({"city": "上海"}, ensure_ascii=False),
                ),
            )
            message = SimpleNamespace(content=None, tool_calls=[tool_call])
        else:
            tool_result = json.loads(kwargs["messages"][-1]["content"])
            weather = tool_result["result"]
            message = SimpleNamespace(
                content=f"查询完成：上海天气为{weather}。",
                tool_calls=None,
            )
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class MockClient:
    def __init__(self) -> None:
        self.chat = SimpleNamespace(completions=MockCompletions())


if __name__ == "__main__":
    client = MockClient()
    dispatch = {"query_weather": lambda city: "晴，25℃"}
    tool_defs = [
        {
            "type": "function",
            "function": {
                "name": "query_weather",
                "description": "查询城市天气",
                "parameters": {
                    "type": "object",
                    "properties": {"city": {"type": "string"}},
                    "required": ["city"],
                },
            },
        }
    ]
    answer = run_agent("你是天气助手。", "上海天气如何？", tool_defs)
    print("最终答案：", answer)
