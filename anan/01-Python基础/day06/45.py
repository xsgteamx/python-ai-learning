# -*- coding: utf-8 -*-
"""
45. Prompt 版本管理与 A/B 测试

保留题目要求的 mock=True 参数；最终演示使用 mock=False，
因此 v1.0 / v2.0 的准确率与耗时来自真实 API 调用，而不是预设结果。
"""

import json
import time
from openai import OpenAI

BASE_URL = "https://api.siliconflow.cn/v1"
API_KEY = "你的API秘钥"
MODEL = "deepseek-ai/DeepSeek-V4-Flash"

client = None

PROMPT_VERSIONS = {
    "v1.0": "请判断客户消息的意图。只返回JSON。",
    "v2.0": """
你是银行客户意图分类助手。

分类标准：
- 账户查询：余额、账户信息等
- 转账汇款：转账、汇款等
- 贷款咨询：贷款、信用卡额度调整等
- 投诉建议：投诉、服务不满等
- 其他：无法归入以上类别

只返回 JSON，格式：
{"intent": "账户查询/转账汇款/贷款咨询/投诉建议/其他"}
""".strip(),
}

test_cases = [
    {"text": "我卡里还有多少钱", "expected_intent": "账户查询"},
    {"text": "我想给朋友汇款5000元", "expected_intent": "转账汇款"},
    {"text": "信用卡额度能从2万提到5万吗", "expected_intent": "贷款咨询"},
    {"text": "你们客服态度太差了，我要投诉", "expected_intent": "投诉建议"},
]


def mock_classify(version_key, text):
    """题目允许的 mock 模式：用简单关键词规则模拟返回。"""
    if "余额" in text or "卡里" in text:
        return "账户查询"
    if "转账" in text or "汇款" in text:
        return "转账汇款"
    if "贷款" in text or "额度" in text:
        return "贷款咨询"
    if "投诉" in text or "态度" in text:
        return "投诉建议"
    return "其他"


def api_classify(version_key, text):
    """真实调用 API 获取当前 Prompt 版本的分类结果。"""
    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": PROMPT_VERSIONS[version_key]},
            {"role": "user", "content": text},
        ],
        response_format={"type": "json_object"},
        temperature=0.1,
    )
    result = json.loads(response.choices[0].message.content)
    return result["intent"]


def ab_test(version_key, test_cases, mock=True):
    """逐条测试，返回版本、准确率、总耗时。"""
    start_time = time.time()
    correct = 0

    for case in test_cases:
        if mock:
            predicted = mock_classify(version_key, case["text"])
        else:
            predicted = api_classify(version_key, case["text"])

        if predicted == case["expected_intent"]:
            correct += 1

    total_time = time.time() - start_time
    return {
        "version": version_key,
        "accuracy": correct / len(test_cases),
        "total_time": total_time,
    }


def main():
    global client

    if API_KEY == "你的API秘钥":
        raise ValueError('请先把 API_KEY = "你的API秘钥" 替换为自己的硅基流动 API Key')

    client = OpenAI(api_key=API_KEY, base_url=BASE_URL)

    # 真实 A/B 测试：明确关闭 mock
    for version in ["v1.0", "v2.0"]:
        result = ab_test(version, test_cases, mock=False)
        print(
            f"{result['version']}："
            f"准确率={result['accuracy']:.2%}，"
            f"耗时={result['total_time']:.4f} 秒"
        )


if __name__ == "__main__":
    main()
