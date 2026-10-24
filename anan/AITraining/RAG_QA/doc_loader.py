"""Markdown/TXT/PDF 加载、标题识别和递归分块；保留来源和章节信息。"""
import hashlib
from dataclasses import dataclass
from pathlib import Path
from langchain_text_splitters import RecursiveCharacterTextSplitter


@dataclass(frozen=True)
class TextChunk:
    id: str
    text: str
    metadata: dict


def _read_document(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {'.md', '.txt'}:
        return path.read_text(encoding='utf-8-sig')
    if suffix == '.pdf':
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise RuntimeError('需要解析 PDF 时执行 pip install pypdf') from exc
        reader = PdfReader(str(path))
        return '\n'.join((page.extract_text() or '') for page in reader.pages)
    raise ValueError(f'不支持的文档类型：{path.suffix}')


def parse_sections(text: str, suffix: str):
    """Markdown 按标题、章、条分段；不强行切断短条款。"""
    title = next((line[2:].strip() for line in text.splitlines()
                  if line.startswith('# ')), '未命名文档')
    chapter = '文档前言'
    article = '元信息'
    buffer = []
    sections = []

    def flush():
        if any(line.strip() for line in buffer):
            sections.append((chapter, article, '\n'.join(buffer).strip()))

    for line in text.splitlines():
        if suffix == '.md' and line.startswith('## '):
            flush()
            chapter = line[3:].strip()
            article = '本章说明'
            buffer = []
        elif suffix == '.md' and line.startswith('### '):
            flush()
            article = line[4:].strip()
            buffer = []
        else:
            buffer.append(line)
    flush()
    return title, sections


def load_chunks(folder: Path, chunk_size: int, chunk_overlap: int):
    """返回各文件的 Chunk 列表，避免将不同源文件混为一个分块。"""
    separators = ['\n\n', '\n', '。', '；', '，', ' ', '']
    result = {}
    for path in sorted(folder.iterdir()):
        if not path.is_file() or path.suffix.lower() not in {'.md', '.txt', '.pdf'}:
            continue
        raw = _read_document(path)
        if not raw.strip():
            continue
        title, sections = parse_sections(raw, path.suffix.lower())
        chunks = []
        content_hash = hashlib.sha256(raw.encode('utf-8')).hexdigest()
        for chapter, article, content in sections:
            # 将条款标题显式拼入每个向量块，便于关键词和语义检索。
            prefix = f'文档：{title}\n章节：{chapter}\n条款：{article}\n'
            capacity = max(120, chunk_size - len(prefix))
            local_splitter = RecursiveCharacterTextSplitter(
                chunk_size=capacity,
                chunk_overlap=min(chunk_overlap, capacity // 3),
                separators=separators,
            )
            for idx, fragment in enumerate(local_splitter.split_text(content)):
                formatted = prefix + fragment
                uid = hashlib.sha256(
                    f'{path.name}|{chapter}|{article}|{idx}|{formatted}'.encode('utf-8')
                ).hexdigest()[:32]
                metadata = {
                    'source': path.name,
                    'title': title,
                    'chapter': chapter,
                    'article': article,
                    'chunk_index': idx,
                    'doc_hash': content_hash,
                    'is_simulated': True,
                }
                chunks.append(TextChunk(uid, formatted, metadata))
        result[path.name] = chunks
    return result
