
# ========== 1. 模拟检索函数 ==========
def mock_retrieve(question, top_k=3):
    """模拟检索结果，按相似度降序返回Top-K。"""

    mock_data = {
        "个人贷款申请条件是什么？": [
            {"source": "信贷政策.md", "score": 0.95},
            {"source": "贷款审批.md", "score": 0.82},
            {"source": "员工制度.md", "score": 0.21}
        ],
        "存款账户如何开户？": [
            {"source": "存款管理.md", "score": 0.93},
            {"source": "账户管理.md", "score": 0.85},
            {"source": "信贷政策.md", "score": 0.35}
        ],
        "银行卡挂失怎么办？": [
            {"source": "客户服务.md", "score": 0.82},
            {"source": "网点管理.md", "score": 0.65},
            {"source": "存款管理.md", "score": 0.42}
        ]
    }

    results = mock_data.get(question, [])
    results = sorted(
        results,
        key=lambda x: x["score"],
        reverse=True
    )
    return results[:top_k]


# ========== 2. 检索质量评估函数 ==========
def evaluate_retrieval(test_questions, retrieve_fn):
    """评估Top-3检索命中率Hit Rate。"""

    total = len(test_questions)
    if total == 0:
        print("测试问题列表为空，无法评估")
        return 0.0

    hit_count = 0

    print("========== RAG检索质量评估 ==========")

    for i, item in enumerate(test_questions, 1):
        question = item["question"]
        expected_source = item["expected_source"]

        # 调用检索函数，获取Top-3结果
        results = retrieve_fn(question, top_k=3)[:3]

        # 提取来源
        sources = [r["source"] for r in results]

        # 判断预期来源是否命中
        is_hit = expected_source in sources

        if is_hit:
            hit_count += 1

        # 获取最高相似度分数
        top_score = max(
            (r["score"] for r in results),
            default=0.0
        )

        # 打印每条测试结果
        mark = "✅" if is_hit else "❌"

        print(f"\n第{i}题：{question}")
        print(f"预期来源：{expected_source}")
        print(f"命中情况：{mark}")
        print(f"top_score：{top_score:.3f}")

    # 计算Hit Rate
    hit_rate = hit_count / total * 100

    print("\n========== 评估汇总 ==========")
    print(f"测试问题数：{total}")
    print(f"命中数量：{hit_count}")
    print(f"Hit Rate@3：{hit_rate:.2f}%")

    return hit_rate


# ========== 3. 构造测试问题 ==========
test_questions = [
    {
        "question": "个人贷款申请条件是什么？",
        "expected_source": "信贷政策.md"
    },
    {
        "question": "存款账户如何开户？",
        "expected_source": "存款管理.md"
    },
    {
        "question": "银行卡挂失怎么办？",
        "expected_source": "银行卡业务.md"
    }
]


# ========== 4. 执行评估 ==========
hit_rate = evaluate_retrieval(
    test_questions,
    mock_retrieve
)
