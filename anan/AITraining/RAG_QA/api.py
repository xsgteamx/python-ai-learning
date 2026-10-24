"""可选：仅本机调试的 FastAPI 接口（无公网鉴权）。"""
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from config import Config
from vectorize_and_store import get_collection
from rag_qa import ask

app = FastAPI(title='第三周 RAG 作业（虚构教学数据）')


class Question(BaseModel):
    question: str = Field(min_length=2, max_length=1000)


@app.get('/health')
def health():
    cfg = Config()
    return {'chroma_count': get_collection(cfg).count(), 'model': cfg.chat_model}


@app.post('/ask')
def ask_api(body: Question):
    try:
        return ask(body.question, Config())
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
