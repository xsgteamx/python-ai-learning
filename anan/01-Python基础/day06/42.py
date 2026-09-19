# -*- coding: utf-8 -*-
"""
42. Few-shot 意图分类（真实 API 版）

硅基流动 OpenAI 兼容接口：
    Base URL: https://api.siliconflow.cn/v1
    API Key : 请把 "你的API秘钥" 替换为自己的 Key
"""

import json
from openai import OpenAI

BASE_URL = "https://api.siliconflow.cn/v1"
API_KEY = "你的API秘钥"
MODEL = "deepseek-ai/DeepSeek-V4-Flash"


def build_few_shot_prompt(examples, categories):
    """把示例按“客户消息：xxx → 意图：xxx”逐行拼装进 Prompt。"""
    lines = [
        "你是银行客户意图分类助手。",
        f"可选意图类别：{', '.join(categories)}",
        "",
        "以下是 Few-shot 分类示例：",
    ]

    for example in examples:
        lines.append(
            f"客户消息：{example['text']} → 意图：{example['intent']}"
        )

    lines.extend([
        "",
        "请对新的客户消息进行意图分类。",
        "只能从上述类别中选择一个意图。",
        "只返回 JSON，不要输出其他文字。",
        '输出格式：{"intent": "类别", "confidence": 0.0}',
        "confidence 必须是 0.0-1.0 之间的数字。",
    ])
    return "\n".join(lines)


categories = [
    "账户查询",
    "转账汇款",
    "贷款咨询",
    "投诉建议",
    "其他",
]

examples = [
    {"text": "我卡里还有多少钱", "intent": "账户查询"},
    {"text": "帮我查一下最近的账户余额", "intent": "账户查询"},
    {"text": "我要给朋友转5000块钱", "intent": "转账汇款"},
    {"text": "我想申请一笔消费贷款", "intent": "贷款咨询"},
    {"text": "你们客服态度太差了，我要投诉", "intent": "投诉建议"},
    {"text": "你们银行周末几点下班", "intent": "其他"},
]

SYSTEM_PROMPT = build_few_shot_prompt(examples, categories)


def classify(client, text):
    """真实调用 API，并把模型 JSON 输出解析为 dict。"""
    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"客户消息：{text}"},
        ],
        response_format={"type": "json_object"},
        temperature=0.1,
    )
    return json.loads(response.choices[0].message.content)


def main():
    if API_KEY == "你的API秘钥":
        raise ValueError('请先把 API_KEY = "你的API秘钥" 替换为自己的硅基流动 API Key')

    client = OpenAI(api_key=API_KEY, base_url=BASE_URL)
    text = "我想问问能不能把信用卡额度从2万提到5万"
    result = classify(client, text)

    print("intent:", result["intent"])
    print("confidence:", result["confidence"])


if __name__ == "__main__":
    main()
