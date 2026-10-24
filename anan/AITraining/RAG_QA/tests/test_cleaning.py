"""不需要连接模型即可运行的基本单元测试。"""
import pytest
from ollama_client import extract_final_answer, OllamaError


def test_leaked_thought_with_no_opening_tag():
    raw = '用户问了什么，应该怎么想……\n</think>\nRAG 是检索增强生成。'
    assert extract_final_answer(raw) == 'RAG 是检索增强生成。'


def test_standard_content():
    assert extract_final_answer('RAG 是检索增强生成。') == 'RAG 是检索增强生成。'


def test_only_unclosed_thinking_rejected():
    with pytest.raises(OllamaError):
        extract_final_answer('<think>我还在想')


def test_no_answer_rejected():
    with pytest.raises(OllamaError):
        extract_final_answer('</think>')
