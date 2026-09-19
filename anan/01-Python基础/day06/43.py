# -*- coding: utf-8 -*-
"""
43. CoT 反洗钱分析（真实 API 版）

说明：reasoning_chain 输出的是“审计步骤摘要”，用于展示每一步的
判断结论与事实依据，不要求模型暴露冗长的内部自由推理过程。
"""

import json
from openai import OpenAI

BASE_URL = "https://api.siliconflow.cn/v1"
API_KEY = "你的API秘钥"
MODEL = "deepseek-ai/DeepSeek-V4-Flash"

COT_AML_PROMPT = r"""
## 角色
你是一名银行反洗钱（AML）高级分析师。
你只能依据输入中的客户背景与交易事实进行判断，不得臆造信息。

## 分析流程
严格按四步执行：
1. 识别异常行为指标：提取有事实依据的异常交易特征。
2. 对照反洗钱监管规则判定风险等级：将每项指标评估为 high / medium / low。
3. 综合评估总体风险：综合指标数量、严重程度、交易链路与可能的合理解释。
4. 输出结构化结论：给出 indicators、overall_risk、reasoning_chain、need_sar。

reasoning_chain 只写四步审计摘要：每一步说明“发现了什么、依据是什么、得到什么结论”，
不要输出冗长的内部自由推理过程。

## 输出 JSON 格式
仅输出合法 JSON，不要输出 Markdown 或额外说明：
{
  "indicators": [
    {
      "indicator": "异常指标名称",
      "evidence": "对应的客观事实依据",
      "risk_level": "high|medium|low"
    }
  ],
  "overall_risk": "high|medium|low",
  "reasoning_chain": [
    "第一步审计摘要：...",
    "第二步审计摘要：...",
    "第三步审计摘要：...",
    "第四步审计摘要：..."
  ],
  "need_sar": true
}
""".strip()


def analyze_transaction(client, info):
    """真实调用硅基流动 API 执行四步 AML 分析。"""
    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": COT_AML_PROMPT},
            {
                "role": "user",
                "content": "请分析以下客户与交易信息：\n"
                + json.dumps(info, ensure_ascii=False, indent=2),
            },
        ],
        response_format={"type": "json_object"},
        temperature=0.1,
    )
    return json.loads(response.choices[0].message.content)


def main():
    if API_KEY == "你的API秘钥":
        raise ValueError('请先把 API_KEY = "你的API秘钥" 替换为自己的硅基流动 API Key')

    client = OpenAI(api_key=API_KEY, base_url=BASE_URL)

    # 题目要求的测试案例：
    # 1) 短期内分散转入后集中转出
    # 2) 转账集中在凌晨
    # 3) 此前长期无交易
    info = {
        "客户背景": {
            "账户此前无交易月数": 14,
            "客户职业": "自由职业",
            "历史风险等级": "low",
        },
        "交易": [
            {"时间": "2026-09-14 01:12", "方向": "转入", "金额": 48000},
            {"时间": "2026-09-14 02:37", "方向": "转入", "金额": 46500},
            {"时间": "2026-09-14 03:05", "方向": "转入", "金额": 51000},
            {"时间": "2026-09-14 03:58", "方向": "转入", "金额": 43900},
            {"时间": "2026-09-14 04:26", "方向": "转入", "金额": 52600},
            {"时间": "2026-09-14 05:03", "方向": "转出", "金额": 232000},
        ],
    }

    result = analyze_transaction(client, info)
    print("overall_risk:", result["overall_risk"])
    print("need_sar:", result["need_sar"])


if __name__ == "__main__":
    main()
