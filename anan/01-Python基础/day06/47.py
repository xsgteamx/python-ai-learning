# -*- coding: utf-8 -*-
"""
47. Prompt 优化改造（真实 API 版）

原代码主要问题（至少 3 个）：
1. 模型选型：贷款预审是结构化分类/抽取/规则判断类任务，不必默认使用高成本推理模型。
2. 消息角色：角色、规则、Schema 应放 system；业务数据应放 user，而不是全部拼在 user 中。
3. Prompt 结构：原 Prompt 只有一句话，缺少四层结构中的行为约束、Schema、Few-shot。
4. 输出约束：只说“输出JSON”不能约束字段名、类型、枚举值、数组长度等。
5. 参数设置：temperature=1.5 随机性过高，不适合稳定预审；题目要求优化为 0.1。
6. 缺少 response_format={"type":"json_object"}。
"""

import json
from openai import OpenAI

BASE_URL = "https://api.siliconflow.cn/v1"
API_KEY = "你的API秘钥"

# 题目要求的模型选型是 deepseek-chat。
# 硅基流动实际调用时使用其平台上的可运行模型 ID：
MODEL_SELECTION = "deepseek-chat"
MODEL = "deepseek-ai/DeepSeek-V4-Flash"

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "pre_audit_result": {
            "type": "string",
            "enum": ["pass", "review", "reject"],
        },
        "risk_points": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 1,
        },
        "suggestion": {
            "type": "string",
            "maxLength": 200,
        },
    },
    "required": ["pre_audit_result", "risk_points", "suggestion"],
}

SYSTEM_PROMPT = f"""
## 角色
你是银行贷款预审分析师。
任务是根据客户提交的贷款申请信息，完成初步风险审核并给出结构化预审结果。

## 行为规范
1. 只能依据用户提供的贷款申请信息进行分析，不得编造数据。
2. 信息完整且风险较低时，pre_audit_result 返回 pass。
3. 信息不足或存在需要人工确认的风险时，pre_audit_result 返回 review。
4. 存在明显高风险或不符合基本申请条件时，pre_audit_result 返回 reject。
5. 不提供投资理财或与贷款预审无关的建议。
6. risk_points 至少包含 1 条；若未发现明显风险，可填写“未发现明显风险点”。
7. 严格按照指定 JSON 输出，不要输出 Markdown 或其他解释文字。

## 输出格式
{json.dumps(OUTPUT_SCHEMA, ensure_ascii=False, indent=2)}

## 示例
输入：客户月收入20000元，工作稳定，已有负债每月3000元，近两年无逾期记录，申请贷款10万元。
输出：{{"pre_audit_result":"pass","risk_points":["未发现明显风险点"],"suggestion":"可进入后续审核流程。"}}

输入：客户仅提供姓名和贷款金额，未提供收入、负债和征信情况。
输出：{{"pre_audit_result":"review","risk_points":["缺少收入信息","缺少负债信息","缺少信用记录"],"suggestion":"补充材料后进行人工复核。"}}
""".strip()


def pre_audit_loan(client, loan_text):
    """优化后的真实 API 调用。"""
    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": loan_text},
        ],
        response_format={"type": "json_object"},
        temperature=0.1,
    )
    return json.loads(response.choices[0].message.content)


def main():
    if API_KEY == "你的API秘钥":
        raise ValueError('请先把 API_KEY = "你的API秘钥" 替换为自己的硅基流动 API Key')

    client = OpenAI(api_key=API_KEY, base_url=BASE_URL)

    loan_text = """
客户年龄35岁，月收入18000元，现有月负债支出4000元，
近两年无逾期记录，本次申请个人消费贷款15万元。
""".strip()

    result = pre_audit_loan(client, loan_text)
    print("题目要求模型选型：", MODEL_SELECTION)
    print("pre_audit_result:", result["pre_audit_result"])
    print("risk_points:", result["risk_points"])
    print("suggestion:", result["suggestion"])


if __name__ == "__main__":
    main()
