
# ========== 1. 余弦相似度计算 ==========
def cosine_similarity(a, b):
    """计算两个向量的余弦相似度。"""
    if len(a) != len(b):
        raise ValueError("两个向量长度必须相同")

    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x ** 2 for x in a) ** 0.5
    norm_b = sum(y ** 2 for y in b) ** 0.5

    if norm_a == 0 or norm_b == 0:
        return 0.0

    return dot / (norm_a * norm_b)


# ========== 2. 离线语义检索函数 ==========
def retrieve(chunks, query_embedding, top_k=3,
             score_threshold=0.3):
    """计算相似度，过滤低分结果，返回Top-K。"""

    if top_k <= 0:
        return []

    results = []

    for chunk in chunks:
        score = cosine_similarity(
            chunk["embedding"], query_embedding
        )

        # 过滤低于阈值的结果
        if score >= score_threshold:
            results.append({
                "content": chunk["content"],
                "source": chunk["source"],
                "score": score
            })

    # 按相似度从高到低排序
    results.sort(key=lambda x: x["score"], reverse=True)

    # 取Top-K，得分保留3位小数
    for item in results[:top_k]:
        item["score"] = round(item["score"], 3)

    return results[:top_k]


# ========== 3. 模拟知识库文本块 ==========
# 向量为人工模拟数据，并非真实Embedding结果
chunks = [
    {
        "content": "个人贷款申请需要满足年龄和信用条件。",
        "source": "信贷政策.md",
        "embedding": [0.9, 0.1, 0.0]
    },
    {
        "content": "银行贷款审批需要审核客户收入及负债。",
        "source": "贷款审批.md",
        "embedding": [0.8, 0.3, 0.0]
    },
    {
        "content": "存款账户应当遵守实名制管理要求。",
        "source": "存款制度.md",
        "embedding": [0.1, 0.8, 0.9]
    },
    {
        "content": "员工应严格遵守考勤管理制度。",
        "source": "考勤制度.md",
        "embedding": [-0.7, 0.2, 0.3]
    }
]


# ========== 4. 模拟用户查询向量 ==========
query = "银行个人贷款申请有什么要求？"
query_embedding = [1.0, 0.0, 0.0]


# ========== 5. 执行检索 ==========
results = retrieve(
    chunks,
    query_embedding,
    top_k=3,
    score_threshold=0.3
)

print("用户问题：", query)
print("检索结果数量：", len(results))
print("-" * 40)

if not results:
    print("未找到符合相似度阈值的相关文档")
else:
    for i, item in enumerate(results, 1):
        print(f"第{i}条：")
        print(f"来源：{item['source']}")
        print(f"内容：{item['content']}")
        print(f"相似度：{item['score']:.3f}")
        print()


# ========== 6. 测试无合格结果的情况 ==========
empty_results = retrieve(
    chunks,
    query_embedding,
    top_k=3,
    score_threshold=0.999
)

print("高阈值检索结果：", empty_results)
