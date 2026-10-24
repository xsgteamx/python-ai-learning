
from types import SimpleNamespace


# ========== 1. 模拟 ChromaDB ==========
class MockCollection:
    def __init__(self, name):
        self.name = name
        self.data = []

    def add(self, ids, embeddings, documents, metadatas):
        """模拟批量添加文本块。"""
        if not (len(ids) == len(embeddings) ==
                len(documents) == len(metadatas)):
            raise ValueError("各参数的列表长度必须一致")

        for i in range(len(ids)):
            self.data.append({
                "id": ids[i],
                "embedding": embeddings[i],
                "document": documents[i],
                "metadata": metadatas[i]
            })

    def count(self):
        """返回集合中的文本块数量。"""
        return len(self.data)

    def get(self, where=None):
        """模拟按元数据过滤查询。"""
        rows = self.data

        if where:
            rows = [
                row for row in rows
                if all(
                    row["metadata"].get(k) == v
                    for k, v in where.items()
                )
            ]

        return {
            "ids": [r["id"] for r in rows],
            "documents": [r["document"] for r in rows],
            "metadatas": [r["metadata"] for r in rows]
        }


class MockPersistentClient:
    def __init__(self, path):
        self.path = path
        self.collections = {}

    def get_or_create_collection(self, name):
        if name not in self.collections:
            self.collections[name] = MockCollection(name)
        return self.collections[name]


# 模拟 chromadb 模块
chromadb = SimpleNamespace(
    PersistentClient=MockPersistentClient
)


# ========== 2. 创建客户端和集合 ==========
client = chromadb.PersistentClient(path="./chroma_db")
collection = client.get_or_create_collection(
    name="bank_policies"
)


# ========== 3. 添加3个文本块 ==========
collection.add(
    ids=["chunk_001", "chunk_002", "chunk_003"],
    embeddings=[
        [0.1, 0.2, 0.3],
        [0.2, 0.3, 0.4],
        [0.5, 0.6, 0.7]
    ],
    documents=[
        "个人贷款申请人应年满18周岁，具有完全民事行为能力。",
        "银行应审核借款人的收入、负债及信用记录。",
        "客户办理存款业务时，应遵守账户实名制要求。"
    ],
    metadatas=[
        {"source": "信贷政策.md"},
        {"source": "信贷政策.md"},
        {"source": "存款管理.md"}
    ]
)


# ========== 4. 查看集合数量 ==========
print("集合名称：", collection.name)
print("文本块总数：", collection.count())


# ========== 5. 按元数据过滤查询 ==========
results = collection.get(
    where={"source": "信贷政策.md"}
)

print("\n查询来源：信贷政策.md")
print("匹配数量：", len(results["ids"]))

for i in range(len(results["ids"])):
    print(f"\nID：{results['ids'][i]}")
    print(f"内容：{results['documents'][i]}")
    print(f"元数据：{results['metadatas'][i]}")
