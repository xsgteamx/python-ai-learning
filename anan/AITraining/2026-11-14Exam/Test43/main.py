"""Test43：上下文预算管理与早期历史压缩。"""

from __future__ import annotations

import json
import re
from typing import Any


def count_tokens(text: str) -> int:
    """按题意用字符长度的一半近似 Token 数。"""
    return len(text) // 2


def _context_tokens(messages: list[dict[str, Any]]) -> int:
    serialized = json.dumps(messages, ensure_ascii=False, separators=(",", ":"))
    return count_tokens(serialized)


def _make_summary(messages: list[dict[str, Any]]) -> dict[str, str]:
    previous_summary = next(
        (str(message.get("content", "")) for message in messages if message.get("name") == "context_summary"),
        "",
    )

    def previous_value(label: str) -> str:
        match = re.search(rf"^{label}：(.*)$", previous_summary, flags=re.MULTILINE)
        return match.group(1).strip() if match else ""

    user_text = " ".join(
        str(message.get("content", "")) for message in messages if message.get("role") == "user"
    )
    assistant_text = " ".join(
        str(message.get("content", ""))
        for message in messages
        if message.get("role") in {"assistant", "tool"}
    )
    goal = previous_value("用户目标") or user_text[:80] or "继续完成当前任务"

    decision_matches = re.findall(r"决定[：:]([^。；\n]+)", assistant_text)
    latest_decision = "；".join(decision_matches[:2]) or assistant_text[:50]
    decisions = "；".join(
        part for part in (previous_value("已做决定")[:60], latest_decision[:50]) if part
    ) or "暂无明确决定"

    unresolved_matches = re.findall(
        r"未决问题(?:是|为|[：:])([^。；\n]+)", previous_summary + " " + assistant_text
    )
    unresolved = "；".join(dict.fromkeys(item.strip() for item in unresolved_matches if item.strip()))
    if not unresolved:
        unresolved = previous_value("未决问题") or "暂无明确未决问题"
    order_ids = list(dict.fromkeys(re.findall(r"SO-\d+", previous_summary + " " + user_text)))
    key_data = "、".join(order_ids[:6]) or f"已压缩 {len(messages)} 条早期消息"
    summary = (
        "[历史摘要]\n"
        f"用户目标：{goal}\n"
        f"已做决定：{decisions[:110]}\n"
        f"关键数据：{key_data}\n"
        f"未决问题：{unresolved}"
    )
    return {"role": "system", "name": "context_summary", "content": summary}


def manage_context(
    messages: list[dict[str, Any]],
    max_tokens: int = 8000,
    reserve_output: int = 800,
) -> tuple[list[dict[str, Any]], int]:
    """持续压缩最早历史，直至上下文为输出预留足够预算。"""
    if max_tokens <= reserve_output:
        raise ValueError("max_tokens 必须大于 reserve_output")

    managed = [dict(message) for message in messages]
    before = _context_tokens(managed)

    while _context_tokens(managed) + reserve_output > max_tokens:
        # 永久保留最初 system prompt；每次把最早最多 8 条非 system 历史合并。
        start = 1 if managed and managed[0].get("role") == "system" else 0
        if start >= len(managed):
            raise ValueError("仅 system prompt 已超出上下文预算")

        existing_summary = (
            start < len(managed) and managed[start].get("name") == "context_summary"
        )
        batch_start = start
        batch_end = min(len(managed), start + (9 if existing_summary else 8))

        # 若只剩一条超长消息，也对它生成定长四要素摘要，保证循环能收敛。
        batch = managed[batch_start:batch_end]
        new_summary = _make_summary(batch)
        candidate = managed[:batch_start] + [new_summary] + managed[batch_end:]
        if _context_tokens(candidate) >= _context_tokens(managed):
            # 摘要本身无法再缩短时，说明给定预算小到不足以容纳最小语义骨架。
            raise ValueError("预算过小，无法在保留四要素的前提下继续压缩")
        managed = candidate

    after = _context_tokens(managed)
    return managed, before - after


if __name__ == "__main__":
    long_messages: list[dict[str, str]] = [
        {"role": "system", "content": "你是项目协作助手，请持续记录目标、决定和问题。"}
    ]
    for turn in range(1, 31):
        long_messages.append(
            {
                "role": "user",
                "content": f"第{turn}轮：目标是完成订单系统升级。关键数据 SO-{1000 + turn}。" + "需求细节" * 80,
            }
        )
        long_messages.append(
            {
                "role": "assistant",
                "content": f"第{turn}轮决定：保留兼容接口；未决问题是压测。" + "分析记录" * 80,
            }
        )

    before_tokens = _context_tokens(long_messages)
    compressed, saved = manage_context(long_messages, max_tokens=3000, reserve_output=500)
    after_tokens = _context_tokens(compressed)
    print(f"压缩前 Token：{before_tokens}")
    print(f"压缩后 Token：{after_tokens}")
    print(f"本次节省 Token：{saved}")
    print(f"压缩后消息数：{len(compressed)}，预算校验：{after_tokens + 500 <= 3000}")
    print(compressed[1]["content"])
