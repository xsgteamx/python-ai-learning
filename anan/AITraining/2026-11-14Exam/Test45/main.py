"""Test45：规则、分类与自动级联组成的模型路由网关。"""

from __future__ import annotations

from typing import Any


LIGHT_MODEL = "light-model"
FLAGSHIP_MODEL = "flagship-model"


def mock_call(model: str, request: dict[str, Any]) -> dict[str, Any]:
    """light 遇到被分类为复杂的请求时明确要求升级。"""
    if model == LIGHT_MODEL and request.get("_complex", False):
        return {"ok": False, "fallback": True, "reason": "任务复杂度超出轻量模型能力"}
    return {"ok": True, "model": model, "answer": f"已处理：{request.get('text', '')[:30]}"}


def route_request(request: dict[str, Any]) -> dict[str, Any]:
    request_type = request.get("type")
    text = str(request.get("text", ""))
    chain: list[str] = []
    routed_request = dict(request)

    if request_type in {"classification", "extraction"}:
        initial_model = LIGHT_MODEL
        chain.append(f"规则命中：{request_type} → light")
    elif request_type == "complex_reasoning":
        initial_model = FLAGSHIP_MODEL
        chain.append("规则命中：complex_reasoning → flagship")
    elif request_type == "auto":
        has_complex_keyword = any(key in text for key in ("分析", "推理", "报告"))
        is_simple = len(text) < 200 and not has_complex_keyword
        complexity_score = min(1.0, len(text) / 200 + (0.6 if has_complex_keyword else 0.0))
        routed_request["_complex"] = not is_simple
        chain.append(
            f"分类结果：{'简单' if is_simple else '复杂'}（复杂度 {complexity_score:.2f}）"
        )
        # auto 采用成本优先的级联：先试 light，复杂任务由模型显式请求升级。
        initial_model = LIGHT_MODEL
        chain.append("级联首选：light")
    else:
        raise ValueError(f"不支持的 request.type：{request_type}")

    result = mock_call(initial_model, routed_request)
    final_model = initial_model
    if not result.get("ok") and result.get("fallback") and initial_model == LIGHT_MODEL:
        chain.append("级联升级：light → flagship")
        final_model = FLAGSHIP_MODEL
        result = mock_call(final_model, routed_request)
    else:
        chain.append("级联升级：否")

    chain.append(f"最终模型：{final_model}")
    print(" | ".join(chain))
    return {**result, "decision_chain": chain, "final_model": final_model}


if __name__ == "__main__":
    requests = [
        {"type": "extraction", "text": "从订单 SO-1001 中抽取订单号"},
        {"type": "complex_reasoning", "text": "推理供应链延迟的根因"},
        {"type": "auto", "text": "把这句话分成三类"},
        {"type": "auto", "text": "请分析并生成完整报告：" + "业务数据与约束条件" * 30},
    ]
    for index, item in enumerate(requests, 1):
        print(f"\n请求 {index}")
        print(route_request(item))
