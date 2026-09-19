import json


def build_prompt(role, constraints, output_schema, few_shot_examples):
    """
    按 Prompt 四层结构拼装 System Prompt

    :param role: str，角色与任务描述
    :param constraints: list[str]，行为约束
    :param output_schema: dict，输出 JSON Schema
    :param few_shot_examples: list[dict]，Few-shot 示例
           每项格式：
           {
               "input": "...",
               "output": "..."
           }

    :return: str，完整 System Prompt
    """
    parts = []

    # L1：元指令层
    parts.append(
        f"## 角色\n"
        f"{role}"
    )

    # L2：行为约束层
    constraint_text = "\n".join(
        f"{i}. {constraint}"
        for i, constraint in enumerate(constraints, start=1)
    )
    parts.append(
        f"## 行为规范\n"
        f"{constraint_text}"
    )

    # L3：输出 Schema 层
    schema_text = json.dumps(
        output_schema,
        ensure_ascii=False,
        indent=2
    )
    parts.append(
        f"## 输出格式\n"
        f"{schema_text}"
    )

    # L4：Few-shot 示例层
    if few_shot_examples:
        example_list = []

        for i, example in enumerate(few_shot_examples, start=1):
            input_text = example["input"]
            output_text = example["output"]

            # 如果输出本身是 dict/list，转换为格式化 JSON
            if isinstance(output_text, (dict, list)):
                output_text = json.dumps(
                    output_text,
                    ensure_ascii=False,
                    indent=2
                )

            example_list.append(
                f"示例{i}\n"
                f"输入：{input_text}\n"
                f"输出：{output_text}"
            )

        parts.append(
            "## 示例\n"
            + "\n\n".join(example_list)
        )

    # 各层之间空一行
    return "\n\n".join(parts)


# =========================
# 信用卡提额审核演示
# =========================

role = (
    "你是银行信用卡风险审核分析师，"
    "任务是根据客户的收入、负债、信用记录等信息，"
    "判断客户的信用卡提额申请是否可以通过。"
)

constraints = [
    "只能依据用户提供的信息进行判断，不得编造客户数据。",
    "不得提供与信用卡提额审核无关的投资或理财建议。",
    "如果关键信息不足，应将 decision 标记为 manual_review。",
    "必须严格按照指定的 JSON 格式输出，不得输出额外解释文字。"
]

output_schema = {
    "type": "object",
    "properties": {
        "decision": {
            "type": "string",
            "enum": [
                "approved",
                "rejected",
                "manual_review"
            ]
        },
        "risk_level": {
            "type": "string",
            "enum": [
                "low",
                "medium",
                "high"
            ]
        },
        "reason": {
            "type": "string",
            "maxLength": 200
        }
    },
    "required": [
        "decision",
        "risk_level",
        "reason"
    ]
}

few_shot_examples = [
    {
        "input": (
            "客户月收入20000元，"
            "月负债支出3000元，"
            "近两年无逾期记录，"
            "当前信用卡额度20000元，"
            "申请提升至30000元。"
        ),
        "output": {
            "decision": "approved",
            "risk_level": "low",
            "reason": "客户收入稳定，负债水平较低，且近两年无逾期记录。"
        }
    },
    {
        "input": (
            "客户月收入8000元，"
            "月负债支出5000元，"
            "近半年存在两次逾期记录，"
            "申请大幅提升信用卡额度。"
        ),
        "output": {
            "decision": "rejected",
            "risk_level": "high",
            "reason": "客户负债水平较高且近期存在多次逾期记录，信用风险较高。"
        }
    },
    {
        "input": (
            "客户申请信用卡提额，"
            "仅提供当前额度为10000元，"
            "未提供收入、负债及信用记录。"
        ),
        "output": {
            "decision": "manual_review",
            "risk_level": "medium",
            "reason": "缺少收入、负债及信用记录等关键审核信息，需要人工复核。"
        }
    }
]


system_prompt = build_prompt(
    role,
    constraints,
    output_schema,
    few_shot_examples
)

print(system_prompt)


'''
## 角色
你是银行信用卡风险审核分析师，任务是根据客户的收入、负债、信用记录等信息，判断客户的信用卡提额申请是否可以通过。

## 行为规范
1. 只能依据用户提供的信息进行判断，不得编造客户数据。
2. 不得提供与信用卡提额审核无关的投资或理财建议。
3. 如果关键信息不足，应将 decision 标记为 manual_review。
4. 必须严格按照指定的 JSON 格式输出，不得输出额外解释文字。

## 输出格式
{
  "type": "object",
  "properties": {
    "decision": {
      "type": "string",
      "enum": [
        "approved",
        "rejected",
        "manual_review"
      ]
    },
    "risk_level": {
      "type": "string",
      "enum": [
        "low",
        "medium",
        "high"
      ]
    },
    "reason": {
      "type": "string",
      "maxLength": 200
    }
  },
  "required": [
    "decision",
    "risk_level",
    "reason"
  ]
}

## 示例
示例1
输入：客户月收入20000元，月负债支出3000元，近两年无逾期记录，当前信用卡额度20000元，申请提升至30000元。
输出：{
  "decision": "approved",
  "risk_level": "low",
  "reason": "客户收入稳定，负债水平较低，且近两年无逾期记录。"
}

示例2
输入：客户月收入8000元，月负债支出5000元，近半年存在两次逾期记录，申请大幅提升信用卡额度。
输出：{
  "decision": "rejected",
  "risk_level": "high",
  "reason": "客户负债水平较高且近期存在多次逾期记录，信用风险较高。"
}

示例3
输入：客户申请信用卡提额，仅提供当前额度为10000元，未提供收入、负债及信用记录。
输出：{
  "decision": "manual_review",
  "risk_level": "medium",
  "reason": "缺少收入、负债及信用记录等关键审核信息，需要人工复核。"
}
运行完成，耗时 60426.20000000298 毫秒

'''