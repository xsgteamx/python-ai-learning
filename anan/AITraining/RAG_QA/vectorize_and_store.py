"""本地 ChromaDB 持久化存储，向量由真实 Ollama BGE-M3 API 生成。"""
import chromadb
from config import Config
from doc_loader import load_chunks
from ollama_client import OllamaClient


def get_collection(config: Config):
    client = chromadb.PersistentClient(path=str(config.db_path))
    collection = client.get_or_create_collection(
        name='simulated_bank_policies',
        metadata={'hnsw:space': 'cosine'},
    )
    return collection


def check_embedding_model(collection, config):
    sample = collection.get(limit=1, include=['metadatas'])
    metas = sample.get('metadatas') or []
    if metas and metas[0].get('embedding_model') != config.embed_model:
        raise RuntimeError(
            '现有 Chroma 索引用了不同的 Embedding 模型。'
            '请另建索引或删除 chroma_db 后重新 index。'
        )


def index_documents(config: Config):
    config.validate()
    client = OllamaClient(config)
    collection = get_collection(config)
    check_embedding_model(collection, config)
    documents = load_chunks(config.docs_path, config.chunk_size, config.chunk_overlap)
    if len(documents) < 3:
        raise RuntimeError(f'只找到 {len(documents)} 份非空制度文档，作业要求至少 3 份')
    files = []
    total_chunks = 0
    all_sources = set(documents)
    # 将 docs/ 中已删除的旧文档从索引里同步删除。
    for meta in (collection.get(include=['metadatas']).get('metadatas') or []):
        source = meta.get('source')
        if source not in all_sources:
            collection.delete(where={'source': source})

    for filename, chunks in documents.items():
        if not chunks:
            continue
        # 先真实生成全部向量；成功后替换该文件旧索引。
        vectors = []
        for start in range(0, len(chunks), 8):
            batch = chunks[start:start + 8]
            vectors.extend(client.embed([c.text for c in batch]))
        old = collection.get(where={'source': filename}, include=[])
        if old.get('ids'):
            collection.delete(ids=old['ids'])
        for start in range(0, len(chunks), 64):
            batch = chunks[start:start + 64]
            collection.upsert(
                ids=[x.id for x in batch],
                documents=[x.text for x in batch],
                embeddings=vectors[start:start + len(batch)],
                metadatas=[{**x.metadata, 'embedding_model': config.embed_model}
                           for x in batch],
            )
        files.append({'source': filename, 'chunks': len(chunks)})
        total_chunks += len(chunks)
        print(f'入库完成：{filename}，{len(chunks)} 个片段', flush=True)
    return {
        'files': files,
        'documents': len(documents),
        'chunks_created': total_chunks,
        'collection_count': collection.count(),
        'embedding_model': config.embed_model,
        'embedding_dimension': 1024,
        'storage': str(config.db_path),
        'source_type': 'AI 生成的虚构银行制度，仅用于课堂实验',
    }
