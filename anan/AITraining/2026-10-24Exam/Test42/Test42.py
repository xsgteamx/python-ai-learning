
def cosine_similarity(a, b):
    """计算两个等长向量的余弦相似度。"""

    # 检查向量长度
    if len(a) != len(b):
        raise ValueError("两个向量的长度必须相同")

    # 计算点积
    dot_product = sum(x * y for x, y in zip(a, b))

    # 计算向量模长
    norm_a = sum(x ** 2 for x in a) ** 0.5
    norm_b = sum(y ** 2 for y in b) ** 0.5

    # 防止除零错误
    if norm_a == 0 or norm_b == 0:
        return 0.0

    # 余弦相似度
    return dot_product / (norm_a * norm_b)


# ========== 1. 构造示例向量 ==========
# 以下为人工构造的模拟向量，并非真实Embedding结果

# 文本1：银行贷款申请条件
vector_a = [0.9, 0.8, 0.1]

# 文本2：个人信贷申请要求（语义相近）
vector_b = [0.85, 0.75, 0.12]

# 文本3：今天的天气很好（语义无关）
vector_c = [0.05, 0.1, 1.0]


# ========== 2. 计算相似度 ==========
similar_score = cosine_similarity(vector_a, vector_b)
unrelated_score = cosine_similarity(vector_a, vector_c)


# ========== 3. 输出结果 ==========
print("余弦相似度测试")
print("-" * 40)

print(f"语义相近文本相似度：{similar_score:.4f}")
print(f"语义无关文本相似度：{unrelated_score:.4f}")

print("-" * 40)

if similar_score > unrelated_score:
    print("测试通过：语义相近文本的相似度更高")
else:
    print("测试失败：相似度结果不符合预期")
