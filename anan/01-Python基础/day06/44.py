def estimate_cost(prompt_tokens, completion_tokens, model):
    """估算单次 API 调用费用，单位：元"""

    price_table = {
        "deepseek-chat": {
            "input": 1,
            "output": 2
        },
        "deepseek-reasoner": {
            "input": 4,
            "output": 16
        }
    }

    if model not in price_table:
        raise ValueError(f"不支持的模型：{model}")

    input_cost = (
        prompt_tokens / 1_000_000
        * price_table[model]["input"]
    )

    output_cost = (
        completion_tokens / 1_000_000
        * price_table[model]["output"]
    )

    return input_cost + output_cost


def select_model(task_type):
    """根据任务类型选择模型"""

    if task_type in ["classification", "extraction"]:
        return "deepseek-chat"

    if task_type in ["reasoning", "math"]:
        return "deepseek-reasoner"

    return "deepseek-chat"


# =========================
# 1. 成本估算
# =========================

prompt_tokens = 800
completion_tokens = 150

chat_cost = estimate_cost(
    prompt_tokens,
    completion_tokens,
    "deepseek-chat"
)

reasoner_cost = estimate_cost(
    prompt_tokens,
    completion_tokens,
    "deepseek-reasoner"
)

print(f"deepseek-chat 单次费用：{chat_cost:.4f} 元")
print(f"deepseek-reasoner 单次费用：{reasoner_cost:.4f} 元")


# =========================
# 2. 模型选型
# =========================

classification_model = select_model("classification")
reasoning_model = select_model("reasoning")

print("classification 任务选型：", classification_model)
print("reasoning 任务选型：", reasoning_model)

'''
deepseek-chat 单次费用：0.0011 元

deepseek-reasoner 单次费用：0.0056 元

classification 任务选型： deepseek-chat

reasoning 任务选型： deepseek-reasoner
'''