
# ========== 1. 定义 RAG Prompt 模板 ==========
RAG_PROMPT_TEMPLATE = """## 角色
你是银行的内部制度咨询专家。
请仅依据以下提供的文档内容回答问题。

## 重要规则
1. 仅依据提供的文档内容回答，不得使用模型训练数据中的知识补充或编造信息。
2. 如果文档中没有相关信息，必须明确回答："根据提供的文档，未找到相关信息"。
3. 回答必须引用来源，标明对应的文档编号和文件名称。
4. 如果需要根据文档进行合理推断，必须明确标注【推断】，并说明推断依据；不得将推断当作确定事实。

## 相关文档
{context}

## 用户问题
{question}
"""


# ========== 2. 定义 Prompt 组装函数 ==========
def assemble_prompt(retrieved, question):
    """将检索结果与用户问题组装为完整Prompt。"""

    context_parts = []

    # 逐条拼接检索文档
    for i, doc in enumerate(retrieved, start=1):
        part = (
            f"[文档{i}] 来源: {doc['source']}\n"
            f"内容: {doc['content']}"
        )
        context_parts.append(part)

    # 合并所有文档
    context = "\n\n".join(context_parts)

    # 无检索结果时的处理
    if not context:
        context = "（无相关文档）"

    # 使用format填充模板
    prompt = RAG_PROMPT_TEMPLATE.format(
        context=context,
        question=question
    )

    return prompt


# ========== 3. 模拟检索结果 ==========
retrieved = [
    {
        "content": "个人贷款申请人应年满18周岁，具有完全民事行为能力。",
        "source": "信贷政策.md",
        "score": 0.95
    },
    {
        "content": "银行应审核借款人的收入、负债及信用记录。",
        "source": "贷款审批制度.md",
        "score": 0.89
    }
]


# ========== 4. 用户问题 ==========
question = "个人贷款申请需要满足哪些条件？"


# ========== 5. 组装并打印 Prompt ==========
final_prompt = assemble_prompt(retrieved, question)

print("========== 最终 RAG Prompt ==========")
print(final_prompt)
