"""集中管理本地 Ollama 与检索设置。"""
from dataclasses import dataclass
import os
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / '.env')


@dataclass(frozen=True)
class Config:
    ollama_url: str = os.getenv('OLLAMA_URL', 'http://192.168.50.101:12354').rstrip('/')
    embed_model: str = os.getenv('OLLAMA_EMBED_MODEL', 'bge-m3:latest')
    chat_model: str = os.getenv('OLLAMA_CHAT_MODEL', 'qwen3:4b')
    chunk_size: int = int(os.getenv('CHUNK_SIZE', '750'))
    chunk_overlap: int = int(os.getenv('CHUNK_OVERLAP', '120'))
    top_k: int = int(os.getenv('TOP_K', '5'))
    threshold: float = float(os.getenv('SIMILARITY_THRESHOLD', '0.20'))
    num_predict: int = int(os.getenv('CHAT_NUM_PREDICT', '1536'))
    temperature: float = float(os.getenv('CHAT_TEMPERATURE', '0.2'))
    db_path: Path = ROOT / 'chroma_db'
    docs_path: Path = ROOT / 'docs'

    def validate(self):
        if not 0 <= self.chunk_overlap < self.chunk_size:
            raise ValueError('CHUNK_OVERLAP 必须小于 CHUNK_SIZE 且非负')
        if not 1 <= self.top_k <= 100:
            raise ValueError('TOP_K 应处于 1~100 之间')
