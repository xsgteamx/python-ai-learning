
def split_text(text, chunk_size=500, overlap=100):
    """使用滑动窗口将长文本分割为重叠文本块。"""

    if chunk_size <= 0 or not 0 <= overlap < chunk_size:
        raise ValueError("参数错误：需满足 0 <= overlap < chunk_size")

    chunks = []
    step = chunk_size - overlap

    for start in range(0, len(text), step):
        end = min(start + chunk_size, len(text))
        chunks.append(text[start:end])

        if end == len(text):
            break

    return chunks


# 模拟银行制度内容（不少于1200字）
text = (
    "第一条：银行应建立完善的信贷管理制度，"
    "严格执行贷款申请、风险评估、审批、发放及贷后管理流程。"
    "所有业务操作必须符合相关监管要求，确保风险可控。\n"
) * 40

# 调用分割函数
chunks = split_text(text, chunk_size=500, overlap=100)

# 打印结果
print(f"原始文本长度：{len(text)} 字符")
print(f"总块数：{len(chunks)}")

for i, chunk in enumerate(chunks, start=1):
    print(f"第{i}块：{len(chunk)} 字符")
