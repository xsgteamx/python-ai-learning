"""Test41：字符相似度 + mock LLM 的两级意图识别。"""

from __future__ import annotations

from typing import Any


def character_overlap(left: str, right: str) -> float:
    """用字符集合的 Jaccard 系数模拟 embedding 相似度。"""
    left_chars = set(left.replace(" ", ""))
    right_chars = set(right.replace(" ", ""))
    if not left_chars and not right_chars:
        return 1.0
    if not left_chars or not right_chars:
        return 0.0
    return len(left_chars & right_chars) / len(left_chars | right_chars)


def mock_llm_classify(text: str) -> dict[str, Any]:
    """固定规则模拟 LLM 分类结果，便于离线复现。"""
    if any(word in text for word in ("什么时候能到", "到货", "送到")):
        return {"intent": "query_logistics", "confidence": 0.72}
    if any(word in text for word in ("退款", "退钱")):
        return {"intent": "request_refund", "confidence": 0.68}
    return {"intent": "unknown", "confidence": 0.23}


def classify_intent(
    text: str,
    intents: list[dict[str, Any]],
    threshold: float = 0.8,
) -> dict[str, Any]:
    if not 0 <= threshold <= 1:
        raise ValueError("threshold 必须位于 0 到 1 之间")
    if not intents:
        raise ValueError("intents 不得为空")

    best_name = ""
    best_score = -1.0
    for intent in intents:
        examples = intent.get("examples", [])
        if len(examples) < 2:
            raise ValueError(f"意图 {intent.get('name')} 至少需要 2 条示例")
        score = max(character_overlap(text, example) for example in examples)
        if score > best_score:
            best_name, best_score = intent["name"], score

    # 题目要求“高于 threshold”才由第一层直接返回，因此这里使用严格大于。
    if best_score > threshold:
        return {"intent": best_name, "layer": "embedding", "score": round(best_score, 3)}

    llm_result = mock_llm_classify(text)
    confidence = float(llm_result["confidence"])
    if confidence < 0.5:
        return {"intent": "clarify", "layer": "none", "score": round(confidence, 3)}
    return {
        "intent": llm_result["intent"],
        "layer": "llm",
        "score": round(confidence, 3),
    }


if __name__ == "__main__":
    intent_examples = [
        {"name": "query_order", "examples": ["查询订单状态", "我的订单到哪一步了"]},
        {"name": "query_logistics", "examples": ["查询物流轨迹", "快递现在在哪里"]},
        {"name": "request_refund", "examples": ["我要申请退款", "请帮我退钱"]},
    ]

    demonstrations = [
        "查询订单状态",       # 高频标准问法，第一层直接命中
        "我买的东西什么时候能到",  # 第一层低于阈值，交给 LLM
        "今天天空为什么是蓝色",   # 两层均无把握，需要澄清
    ]
    for text in demonstrations:
        print(text, "=>", classify_intent(text, intent_examples))
