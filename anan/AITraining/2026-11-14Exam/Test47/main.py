"""Test47：问题 Agent 的分析与可靠性改造。"""

from __future__ import annotations

import json
import time
from types import SimpleNamespace
from typing import Any, Callable


PROBLEMS = [
    "while True 没有最大轮次限制，模型持续请求工具时会形成死循环。",
    "没有捕获模型、JSON 解析和工具执行异常，任一异常都会让 Agent 崩溃。",
    "工具结果被错误地伪装成 assistant 消息，缺少 role=tool 和 tool_call_id。",
    "没有把 assistant 的 tool_calls 原消息加入历史，对话协议不完整。",
    "没有区分超时/连接错误与业务错误，可能重试不可恢复错误或漏掉可恢复错误。",
    "失败没有结构化 code/message/hint，模型无法把技术错误翻译成用户可执行建议。",
    "连续失败没有熔断，会持续冲击已故障的下游服务。",
]


def _assistant_dict(message: Any) -> dict[str, Any]:
    data: dict[str, Any] = {"role": "assistant", "content": getattr(message, "content", None)}
    calls = getattr(message, "tool_calls", None) or []
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


def _execute_tool(
    tool_fn: Callable[..., Any],
    arguments: dict[str, Any],
    max_tool_retries: int,
) -> dict[str, Any]:
    """只重试暂时性故障；业务/参数错误直接返回。"""
    for attempt in range(1, max_tool_retries + 1):
        try:
            return {"ok": True, "result": tool_fn(**arguments)}
        except (TimeoutError, ConnectionError) as exc:
            if attempt == max_tool_retries:
                return {
                    "ok": False,
                    "code": "TEMPORARY_UNAVAILABLE",
                    "message": str(exc),
                    "hint": "服务暂时繁忙，请稍后重试",
                    "retryable": True,
                }
            time.sleep(0.05 * (2 ** (attempt - 1)))
        except (ValueError, TypeError, KeyError) as exc:
            return {
                "ok": False,
                "code": "BIZ_ERROR",
                "message": str(exc),
                "hint": "请核对输入信息后重试",
                "retryable": False,
            }
        except Exception as exc:  # 防止未知工具异常击穿主循环
            return {
                "ok": False,
                "code": "INTERNAL_ERROR",
                "message": str(exc),
                "hint": "工具发生未知错误，请联系支持人员",
                "retryable": False,
            }
    raise AssertionError("unreachable")


def flaky_agent(
    task: str,
    client: Any,
    dispatch: dict[str, Callable[..., Any]],
    max_turns: int = 5,
    failure_threshold: int = 3,
    max_tool_retries: int = 2,
) -> str:
    """修正后的完整 Agent：轮次兜底、分类重试、规范回填及熔断。"""
    messages: list[dict[str, Any]] = [{"role": "user", "content": task}]
    consecutive_failures = 0

    for _ in range(max_turns):
        try:
            response = client.chat.completions.create(
                model="deepseek-chat",
                messages=messages,
            )
        except (TimeoutError, ConnectionError) as exc:
            consecutive_failures += 1
            if consecutive_failures >= failure_threshold:
                return "服务连续失败，熔断器已开启，当前暂时不可用，请稍后再试。"
            messages.append({
                "role": "system",
                "content": json.dumps({
                    "ok": False, "code": "MODEL_TEMPORARY_ERROR", "message": str(exc),
                    "hint": "模型服务暂时不可用，请稍后重试",
                }, ensure_ascii=False),
            })
            continue

        message = response.choices[0].message
        messages.append(_assistant_dict(message))
        calls = getattr(message, "tool_calls", None) or []
        if not calls:
            return message.content or ""

        for call in calls:
            try:
                arguments = json.loads(call.function.arguments or "{}")
                tool_fn = dispatch[call.function.name]
                result = _execute_tool(tool_fn, arguments, max_tool_retries)
            except json.JSONDecodeError as exc:
                result = {
                    "ok": False, "code": "INVALID_ARGUMENT_JSON", "message": str(exc),
                    "hint": "请按合法 JSON 重新生成工具参数", "retryable": False,
                }
            except KeyError:
                result = {
                    "ok": False, "code": "UNKNOWN_TOOL",
                    "message": f"未注册工具：{call.function.name}",
                    "hint": "请改用已注册工具", "retryable": False,
                }

            if result["ok"]:
                consecutive_failures = 0
            else:
                consecutive_failures += 1

            # 成功或失败都必须用 tool 角色及原 tool_call_id 回填，让模型感知结果。
            messages.append({
                "role": "tool",
                "tool_call_id": call.id,
                "content": json.dumps(result, ensure_ascii=False),
            })

            if consecutive_failures >= failure_threshold:
                return "工具服务连续失败，熔断器已开启，当前暂时不可用，请稍后再试。"

    return "已达最大轮次，任务未完成"


class DemoCompletions:
    def __init__(self) -> None:
        self.count = 0

    def create(self, **kwargs: Any) -> Any:
        self.count += 1
        if self.count == 1:
            call = SimpleNamespace(
                id="order-call-1",
                function=SimpleNamespace(name="query_order", arguments='{"order_id":"SO-1001"}'),
            )
            message = SimpleNamespace(content=None, tool_calls=[call])
        else:
            tool_payload = json.loads(kwargs["messages"][-1]["content"])
            message = SimpleNamespace(
                content=f"订单状态：{tool_payload['result']['status']}", tool_calls=None
            )
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


if __name__ == "__main__":
    print("原代码问题：")
    for index, problem in enumerate(PROBLEMS, 1):
        print(f"{index}. {problem}")

    demo_client = SimpleNamespace(chat=SimpleNamespace(completions=DemoCompletions()))
    demo_dispatch = {"query_order": lambda order_id: {"order_id": order_id, "status": "已发货"}}
    print("\n修正代码演示：", flaky_agent("查询 SO-1001", demo_client, demo_dispatch))
