
import json


# ========== 1. 模拟检索 ==========
def mock_retrieve(question):
    """模拟检索，返回固定的文档块及相似度。"""

    if "贷款" in question:
        return [
            {
                "content": "个人贷款申请人须年满18周岁。",
                "source": "信贷政策.md",
                "score": 0.95678
            },
            {
                "content": "银行应审核借款人的收入及信用记录。",
                "source": "贷款审批制度.md",
                "score": 0.85234
            },
            {
                "content": "员工应遵守考勤管理规定。",
                "source": "考勤制度.md",
                "score": 0.15
            }
        ]

    # 模拟未找到相关文档
    return [
        {
            "content": "银行员工应遵守日常管理制度。",
            "source": "员工制度.md",
            "score": 0.20
        },
        {
            "content": "银行应加强内部业务管理。",
            "source": "管理制度.md",
            "score": 0.30
        }
    ]


# ========== 2. 模拟 LLM ==========
def mock_llm(prompt):
    """模拟大模型生成回答，不进行真实API调用。"""
    return "【模拟LLM回答】\n" + prompt


# ========== 3. RAG 问答编排 ==========
def ask(question, top_k=3):
    """执行检索、过滤、组装、生成四个步骤。"""

    # Step1：检索
    retrieved = mock_retrieve(question)

    # Step2：过滤 score <= 0.3 的结果
    valid_docs = [
        doc for doc in retrieved
        if doc["score"] > 0.3
    ]

    # 按相似度降序，取Top-K
    valid_docs.sort(
        key=lambda x: x["score"],
        reverse=True
    )
    valid_docs = valid_docs[:max(0, top_k)]

    # 无合格结果，直接返回
    if not valid_docs:
        return {
            "answer": "根据提供的文档，未找到与您问题相关的信息。",
            "sources": []
        }

    # Step3：组装context
    context = "\n\n".join(
        f"[文档{i}] 来源: {doc['source']}\n"
        f"内容: {doc['content']}"
        for i, doc in enumerate(valid_docs, 1)
    )

    prompt = (
        "你是银行内部制度咨询专家。\n"
        "请仅根据提供的文档回答问题，"
        "并引用对应的文档来源。\n\n"
        f"## 相关文档\n{context}\n\n"
        f"## 用户问题\n{question}"
    )

    # Step4：调用LLM生成回答（模拟）
    answer = mock_llm(prompt)

    # 整理来源，score保留3位小数
    sources = [
        {
            "source": doc["source"],
            "score": round(doc["score"], 3)
        }
        for doc in valid_docs
    ]

    return {
        "answer": answer,
        "sources": sources
    }


# ========== 4. 测试：命中 ==========
print("========== 测试1：命中 ==========")

result1 = ask("个人贷款申请需要什么条件？")

print(json.dumps(
    result1,
    ensure_ascii=False,
    indent=2
))


# ========== 5. 测试：未命中 ==========
print("\n========== 测试2：未命中 ==========")

result2 = ask("公司的食堂午餐有什么？")

print(json.dumps(
    result2,
    ensure_ascii=False,
    indent=2
))
