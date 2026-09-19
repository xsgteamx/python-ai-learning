# -*- coding: utf-8 -*-
"""
46. 多模态票据流水线（真实 API 版）

本题按题意分两段：
1. image_to_base64：负责本地图片 Base64 编码与 FileNotFoundError 处理。
2. extract_fields：把已经识别出的票据 raw_text 发给真实大模型做结构化提取。
"""

import base64
import json
from openai import OpenAI

BASE_URL = "https://api.siliconflow.cn/v1"
API_KEY = "你的API秘钥"
MODEL = "deepseek-ai/DeepSeek-V4-Flash"


def image_to_base64(image_path):
    """读取本地图片文件并进行 Base64 编码后返回字符串。"""
    try:
        with open(image_path, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")
    except FileNotFoundError:
        print(f"图片文件不存在：{image_path}")
        return None


def extract_fields(client, raw_text):
    """真实调用 API，从票据识别文本中提取结构化字段。"""
    schema = {
        "type": "object",
        "properties": {
            "票据类型": {
                "type": "string",
                "enum": ["转账凭证", "收款凭证", "发票", "其他"],
            },
            "金额": {"type": "number"},
            "付款方": {"type": "string"},
            "收款方": {"type": "string"},
            "日期": {
                "type": "string",
                "description": "日期必须使用 YYYY-MM-DD 格式",
            },
            "是否可疑": {"type": "boolean"},
        },
        "required": ["票据类型", "金额", "付款方", "收款方", "日期", "是否可疑"],
    }

    system_prompt = (
        "你是银行票据信息提取助手。"
        "只能依据输入的票据文本提取信息，不得编造。"
        "严格按照以下 JSON Schema 输出，只返回 JSON：\n"
        + json.dumps(schema, ensure_ascii=False, indent=2)
    )

    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": raw_text},
        ],
        response_format={"type": "json_object"},
        temperature=0.1,
    )
    return json.loads(response.choices[0].message.content)


def main():
    # 演示 1：不存在的图片路径
    image_base64 = image_to_base64("not_exist_receipt.jpg")
    print("Base64结果：", image_base64)

    if API_KEY == "你的API秘钥":
        raise ValueError('请先把 API_KEY = "你的API秘钥" 替换为自己的硅基流动 API Key')

    client = OpenAI(api_key=API_KEY, base_url=BASE_URL)

    # 演示 2：真实 API 结构化提取
    raw_text = """
中国某银行转账凭证
日期：2026-09-19
付款方：北京甲公司
收款方：上海乙公司
金额：人民币壹万贰仟伍佰元整（12500.00元）
备注：货款
""".strip()

    result = extract_fields(client, raw_text)
    print("付款方：", result["付款方"])
    print("金额：", result["金额"])


if __name__ == "__main__":
    main()
