"""Test42：指数退避、幂等键与业务错误隔离。"""

from __future__ import annotations

import time
from typing import Any, Callable


# 正式环境可改为 1.0；演示按题意使用 0.1 倍速。
SLEEP_SCALE = 0.1


def call_tool_with_retry(
    tool_fn: Callable[..., Any],
    args: dict[str, Any],
    max_retries: int = 3,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """调用工具；max_retries 表示首次调用失败后最多再重试多少次。"""
    if max_retries < 0:
        raise ValueError("max_retries 不得小于 0")

    headers: dict[str, str] = {}
    if idempotency_key:
        # 服务端可据此识别同一逻辑请求，避免重试造成重复扣款、重复下单等副作用。
        headers["Idempotency-Key"] = idempotency_key

    last_error = ""
    # 总尝试次数 = 首次调用 1 次 + 最多 max_retries 次重试。
    for attempt in range(1, max_retries + 2):
        try:
            result = tool_fn(args, headers=headers)
            return {"ok": True, "result": result, "attempts": attempt}
        except ValueError as exc:
            # 订单不存在等业务错误重试也不会恢复，必须立刻返回。
            return {
                "ok": False,
                "code": "BIZ_ERROR",
                "message": str(exc),
                "hint": "请与用户确认订单号后重试",
            }
        except (TimeoutError, ConnectionError) as exc:
            last_error = str(exc)
            retries_used = attempt - 1
            if retries_used >= max_retries:
                break
            delay = 2 ** retries_used  # 1s、2s、4s……
            print(f"第 {attempt} 次调用失败：{exc}；{delay}s 后重试")
            time.sleep(delay * SLEEP_SCALE)

    return {
        "ok": False,
        "code": "RETRY_EXCEEDED",
        "message": last_error,
        "hint": "多次连接工具失败，请稍后重试；不要立即重复提交业务请求",
    }


class FlakyMockTool:
    def __init__(self) -> None:
        self.calls = 0
        self.seen_keys: list[str | None] = []

    def __call__(self, args: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
        self.calls += 1
        self.seen_keys.append(headers.get("Idempotency-Key"))
        print(f"工具第 {self.calls} 次收到幂等键：{headers.get('Idempotency-Key')}")
        if self.calls <= 2:
            raise TimeoutError("上游响应超时")
        return {"order_id": args["order_id"], "status": "PAID"}


if __name__ == "__main__":
    mock_tool = FlakyMockTool()
    result = call_tool_with_retry(
        mock_tool,
        {"order_id": "SO-1001"},
        max_retries=3,
        idempotency_key="order-query-1001",
    )
    print("调用结果：", result)
    print("三次调用使用同一幂等键：", len(set(mock_tool.seen_keys)) == 1)
