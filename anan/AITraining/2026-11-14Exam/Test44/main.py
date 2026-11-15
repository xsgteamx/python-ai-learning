"""Test44：分层记忆与写入三道闸。"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


class MemoryStore:
    REQUIRED_FIELDS = {"subject", "predicate", "value", "source", "confidence"}

    def __init__(self) -> None:
        self.profile: list[dict[str, Any]] = []
        self.episode: list[dict[str, Any]] = []
        self.current_round = 0

    def write(self, memory: dict[str, Any]) -> dict[str, Any]:
        # 第一闸：一次性寒暄不值得长期记忆。
        if memory.get("one_shot") is True:
            return {"ok": False, "reason": "一次性内容不值得记忆"}

        # 第二闸：只能写入明确指定的合法层。
        layer = memory.get("layer")
        if layer not in {"profile", "episode"}:
            return {"ok": False, "reason": "layer 必须是 profile 或 episode"}

        # 第三闸：表达必须完整、结构化。
        missing = sorted(self.REQUIRED_FIELDS - memory.keys())
        if missing:
            return {"ok": False, "reason": f"缺少结构化字段：{', '.join(missing)}"}
        if not isinstance(memory["confidence"], (int, float)):
            return {"ok": False, "reason": "confidence 必须是数值"}

        target = self.profile if layer == "profile" else self.episode
        new_memory = deepcopy(memory)
        if layer == "episode":
            ttl = int(new_memory.get("ttl", 1))
            if ttl <= 0:
                return {"ok": False, "reason": "episode 的 ttl 必须大于 0"}
            new_memory["expires_at"] = self.current_round + ttl

        # 同主体、同谓词但值变化时，旧事实降权，仍保留新旧两条以便追溯。
        for old in self.profile + self.episode:
            if (
                old["subject"] == new_memory["subject"]
                and old["predicate"] == new_memory["predicate"]
                and old["value"] != new_memory["value"]
            ):
                old["confidence"] = round(max(0.0, float(old["confidence"]) - 0.2), 3)

        target.append(new_memory)
        return {"ok": True, "layer": layer, "memory": deepcopy(new_memory)}

    def advance_round(self, steps: int = 1) -> None:
        if steps < 0:
            raise ValueError("steps 不得为负数")
        self.current_round += steps

    def recall(self, subject: str) -> list[dict[str, Any]]:
        active_profile = [item for item in self.profile if item["subject"] == subject]
        active_episode = [
            item
            for item in self.episode
            if item["subject"] == subject and self.current_round < item["expires_at"]
        ]
        return deepcopy(active_profile + active_episode)


if __name__ == "__main__":
    store = MemoryStore()
    print("写入画像：", store.write({
        "layer": "profile", "subject": "user-1", "predicate": "preferred_language",
        "value": "中文", "source": "用户明确说明", "confidence": 0.95,
    }))
    print("写入情景：", store.write({
        "layer": "episode", "subject": "user-1", "predicate": "current_project",
        "value": "订单系统升级", "source": "本轮对话", "confidence": 0.9, "ttl": 3,
    }))
    print("写入寒暄：", store.write({
        "layer": "episode", "one_shot": True, "subject": "user-1", "predicate": "greeting",
        "value": "你好", "source": "本轮对话", "confidence": 1.0,
    }))
    print("写入冲突记忆：", store.write({
        "layer": "profile", "subject": "user-1", "predicate": "preferred_language",
        "value": "English", "source": "用户最新说明", "confidence": 0.98,
    }))
    print("当前有效记忆：", store.recall("user-1"))
    store.advance_round(3)
    print("3 轮后（情景记忆过期）：", store.recall("user-1"))
