"""命令行入口：index/search/ask/smoke/eval。"""
import argparse
import csv
import json
from pathlib import Path
from config import Config
from ollama_client import OllamaClient, OllamaError
from vectorize_and_store import index_documents
from rag_qa import retrieve, ask


def print_json(data):
    print(json.dumps(data, ensure_ascii=False, indent=2))


def smoke(config):
    client = OllamaClient(config)
    models = client.list_models()
    required = [config.embed_model, config.chat_model]
    missing = [m for m in required if m not in models]
    if missing:
        raise RuntimeError(f'Ollama 缺少模型 {missing}；实际模型列表：{models}')
    embedding = client.embed(['银行知识库向量接口测试'])[0]
    response = client.chat('直接用中文回答，不要输出分析。', '用一句话解释RAG。')
    return {
        'ollama_url': config.ollama_url,
        'models': required,
        'embedding_dimension': len(embedding),
        'chat_answer': response['answer'],
        'chat_done_reason': response['done_reason'],
        'passed': True,
    }


def run_eval(config):
    data = json.loads((Path(__file__).parent / 'tests' / 'golden_questions.json').read_text(encoding='utf-8'))
    output = []
    for item in data:
        chunks = retrieve(item['question'], config)
        sources = {chunk['source'] for chunk in chunks}
        hit = item['expected_source'] in sources
        output.append({
            'question': item['question'],
            'expected_source': item['expected_source'],
            'hit': int(hit),
            'top1_source': chunks[0]['source'] if chunks else '',
            'top1_similarity': chunks[0]['similarity'] if chunks else '',
        })
        print(f'{"HIT" if hit else "MISS"}: {item["question"]}', flush=True)
    outpath = Path(__file__).parent / 'retrieval_eval.csv'
    with outpath.open('w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=output[0].keys())
        w.writeheader()
        w.writerows(output)
    return {
        'questions': len(output),
        'hit_rate_at_k': sum(x['hit'] for x in output) / len(output),
        'top_k': config.top_k,
        'report': str(outpath),
        'note': '这是实际检索得到的命中率，不代表生成答案准确率',
    }


def main():
    parser = argparse.ArgumentParser(description='第三周 RAG 银行模拟制度问答')
    parser.add_argument('command', choices=['smoke', 'index', 'search', 'ask', 'eval'])
    parser.add_argument('question', nargs='?', default='')
    args = parser.parse_args()
    config = Config()
    try:
        if args.command == 'smoke':
            result = smoke(config)
        elif args.command == 'index':
            result = index_documents(config)
        elif args.command == 'eval':
            result = run_eval(config)
        elif args.command == 'search':
            if not args.question:
                parser.error('search 需要提供问题文本')
            result = {'question': args.question, 'retrieved_chunks': retrieve(args.question, config)}
        else:
            if not args.question:
                parser.error('ask 需要提供问题文本')
            result = ask(args.question, config)
        print_json(result)
    except (OllamaError, RuntimeError, ValueError, OSError) as e:
        parser.exit(1, f'执行失败：{e}\n')


if __name__ == '__main__':
    main()
