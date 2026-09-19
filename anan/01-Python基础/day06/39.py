# -*- coding: utf-8 -*-
"""
System Prompt 拼装器：按「四层结构」生成结构化 System Prompt。

四层结构：
    L1 角色      -> "## 角色"     + 角色内容
    L2 行为规范  -> "## 行为规范" + 逐条编号的约束
    L3 输出格式  -> "## 输出格式" + json.dumps(..., ensure_ascii=False, indent=2)
    L4 示例      -> "## 示例"     + 每组「输入：… / 输出：…」（few_shot_examples 为空则省略）
层与层之间用一个空行（即 "\n\n"）分隔。
"""

import json
from typing import Any, Dict, List, Sequence, Tuple, Union

# 一组示例可以是 {"input": ..., "output": ...} 字典，也可以是 (input, output) 二元组
Example = Union[Dict[str, Any], Tuple[Any, Any], Sequence[Any]]


def _stringify(value: Any) -> str:
    """把任意值转成可放进 Prompt 里的字符串：容器走 JSON，其他走 str()。"""
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def build_prompt(role: str,
                 constraints: Sequence[str],
                 output_schema: Any,
                 few_shot_examples: Sequence[Example] = ()) -> str:
    """
    按四层结构拼装 System Prompt。

    参数
    ----
    role : str
        角色描述（L1）。
    constraints : Sequence[str]
        行为规范条目（L2），按传入顺序自动编号。
    output_schema : Any
        期望的输出结构（L3），通常是 dict，会被序列化为缩进 2 格的 JSON。
    few_shot_examples : Sequence[Example]
        少样本示例（L4），每项为 {"input":..., "output":...} 或 (input, output)。
        为空时不输出该层。

    返回
    ----
    str
        拼装好的 System Prompt。
    """
    # --- 参数校验（EAFP 之外的显式守卫：这里更该提前报错而不是产出半个 Prompt）---
    if not isinstance(role, str) or not role.strip():
        raise ValueError("role 必须是非空字符串")
    if constraints is None:
        raise ValueError("constraints 不能为 None")
    if few_shot_examples is None:
        few_shot_examples = ()

    layers: List[str] = []

    # L1 角色
    layers.append("## 角色\n" + role.strip())

    # L2 行为规范（逐条编号）
    constraint_lines = ["## 行为规范"]
    for index, item in enumerate(constraints, start=1):
        constraint_lines.append(f"{index}. {item}")
    layers.append("\n".join(constraint_lines))

    # L3 输出格式
    schema_text = json.dumps(output_schema, ensure_ascii=False, indent=2)
    layers.append("## 输出格式\n```json\n" + schema_text + "\n```")

    # L4 示例（可选层）
    if few_shot_examples:
        example_blocks: List[str] = []
        for pair in few_shot_examples:
            if isinstance(pair, dict):
                input_part = pair.get("input", "")
                output_part = pair.get("output", "")
            else:  # (input, output) 二元组
                input_part, output_part = pair[0], pair[1]
            example_blocks.append(
                f"输入：{_stringify(input_part)}\n"
                f"输出：{_stringify(output_part)}"
            )
        layers.append("## 示例\n" + "\n\n".join(example_blocks))

    # 层与层之间空一行
    return "\n\n".join(layers)


# ---------------------------------------------------------------------------
# 演示：信用卡提额审核
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    role = (
        "你是某银行信用卡中心的资深提额审核专家，"
        "需要基于客户的资质与用卡行为，判断本次提额申请是否通过，"
        "并给出可追溯、可解释的审批结论。"
    )

    constraints = [
        "只依据输入中给出的客观字段判断，禁止臆造收入、负债、征信等未提供的数据。",
        "提额后额度不得超过监管上限，且单次提额比例不得超过行内政策规定的 50%。",
        "近 12 个月存在逾期记录、疑似套现行为，或负债率超过 70% 的客户，一律不予提额。",
        "每条结论必须附带至少一条具体、可追溯的判定依据（引用输入中的真实字段）。",
        "只输出符合「输出格式」的 JSON，不要输出任何额外说明文字。",
    ]

    output_schema = {
        "decision": "字符串：approve（通过）/ reject（拒绝）/ manual_review（转人工）",
        "current_limit": "整数：当前额度（元）",
        "approved_limit": "整数：核准后额度（元），拒绝时与当前额度一致",
        "increase_ratio": "浮点数：提额比例，如 0.3 表示上浮 30%",
        "risk_level": "字符串：low / medium / high",
        "reasons": ["字符串数组：至少一条可追溯的判定依据"],
        "conditions": ["字符串数组：通过时需附加的风控条件，可为空数组"],
        "next_review_months": "整数：建议下次可申请提额的间隔月数",
    }

    few_shot_examples = [
        {
            "input": {
                "客户编号": "C100238",
                "当前额度": 30000,
                "申请额度": 45000,
                "月收入": 18000,
                "负债率": 0.32,
                "近12个月逾期次数": 0,
                "持卡月数": 26,
                "月均消费": 8600,
                "疑似套现标记": False,
            },
            "output": {
                "decision": "approve",
                "current_limit": 30000,
                "approved_limit": 42000,
                "increase_ratio": 0.4,
                "risk_level": "low",
                "reasons": [
                    "近12个月逾期次数为 0，还款记录良好",
                    "负债率 0.32 低于 0.70 阈值",
                    "持卡 26 个月且月均消费 8600 元，用卡活跃",
                ],
                "conditions": ["提额后 3 个月内不得再次申请"],
                "next_review_months": 6,
            },
        },
        {
            "input": {
                "客户编号": "C100517",
                "当前额度": 20000,
                "申请额度": 40000,
                "月收入": 9000,
                "负债率": 0.78,
                "近12个月逾期次数": 2,
                "持卡月数": 14,
                "月均消费": 6200,
                "疑似套现标记": False,
            },
            "output": {
                "decision": "reject",
                "current_limit": 20000,
                "approved_limit": 20000,
                "increase_ratio": 0.0,
                "risk_level": "high",
                "reasons": [
                    "近12个月逾期次数为 2，命中「存在逾期即拒」规则",
                    "负债率 0.78 超过 0.70 阈值",
                ],
                "conditions": [],
                "next_review_months": 12,
            },
        },
    ]

    prompt = build_prompt(role, constraints, output_schema, few_shot_examples)
    print(prompt)

    # 顺手落盘，方便直接粘到模型后台或做回归对比
    with open("credit_limit_system_prompt.txt", "w", encoding="utf-8") as f:
        f.write(prompt)
    print("\n--- 已写入 credit_limit_system_prompt.txt ---")

    # 边界验证：few_shot_examples 为空时不输出示例层
    short = build_prompt("测试角色", ["约束 A"], {"a": 1}, [])
    print("\n[无示例时的层数] ->", len(short.split("\n\n")), "层")
    print(short)


'''
## 角色
你是某银行信用卡中心的资深提额审核专家，需要基于客户的资质与用卡行为，判断本次提额申请是否通过，并给出可追溯、可解释的审批结论。

## 行为规范
1. 只依据输入中给出的客观字段判断，禁止臆造收入、负债、征信等未提供的数据。
2. 提额后额度不得超过监管上限，且单次提额比例不得超过行内政策规定的 50%。
3. 近 12 个月存在逾期记录、疑似套现行为，或负债率超过 70% 的客户，一律不予提额。
4. 每条结论必须附带至少一条具体、可追溯的判定依据（引用输入中的真实字段）。
5. 只输出符合「输出格式」的 JSON，不要输出任何额外说明文字。

## 输出格式
```json
{
  "decision": "字符串：approve（通过）/ reject（拒绝）/ manual_review（转人工）",
  "current_limit": "整数：当前额度（元）",
  "approved_limit": "整数：核准后额度（元），拒绝时与当前额度一致",
  "increase_ratio": "浮点数：提额比例，如 0.3 表示上浮 30%",
  "risk_level": "字符串：low / medium / high",
  "reasons": [
    "字符串数组：至少一条可追溯的判定依据"
  ],
  "conditions": [
    "字符串数组：通过时需附加的风控条件，可为空数组"
  ],
  "next_review_months": "整数：建议下次可申请提额的间隔月数"
}
'''