# 第三周 AI 培训｜RAG 2.0 银行制度知识库问答（Ollama 免费本地版）

本项目用于第三周课堂作业。**所有制度都是 AI 生成的虚构教学资料，不能用于实际金融业务；程序通过局域网调用Ollama，未使用任何付费第三方 API，不包含模拟向量或伪造的模型回答。**

## 1. 系统架构

```text
Windows / PyCharm                              LAN-Ollama
├─ docs/：3 份 Markdown 模拟银行制度              LAN-Ollama URL
├─ doc_loader.py：章节识别和递归切块                  ├─ bge-m3:latest （1024 维）
├─ vectorize_and_store.py：ChromaDB 持久化          └─ qwen3:4b      （聊天）
├─ rag_qa.py：retrieve()/ask()
├─ ollama_client.py：真实 HTTP 接口/思考内容清洗
├─ main.py：命令行
└─ api.py：可选 FastAPI
```

技术原理：原文 → 按章、条加载 → 递归切块 → BGE-M3 真实向量化 → ChromaDB → 用户问题向量化 → 余弦 Top-K → 相关片段 + Prompt → Qwen3 真实回答 → 输出 JSON/引用。

## 2. 环境（Windows PowerShell）

推荐 Python 3.11，先在 PyCharm 终端进入解压后的项目根目录：

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
Copy-Item .env.example .env
```

如果没有 Python 3.11，可使用已安装的兼容 Python 版本新建虚拟环境。PowerShell 不允许激活时，可直接用 `.\.venv\Scripts\python.exe` 代替 `python`（不要改系统策略）。

编辑 `.env`，确定 `OLLAMA_URL` 正确。此地址仅在你的可信局域网可用；**不要把无鉴权的 Ollama 端口映射到公网**。

## 3. 逐步运行（按顺序）

### 第一步：真实模型接口验证

```powershell
python main.py smoke
```

真实调用 `/api/tags`、`/api/embed`、`/api/chat`。成功应包含 `passed: true` 和 `embedding_dimension: 1024`，回答仍由实际 Qwen3 生成。Qwen3 的 `think:false` 在部分版本中可能把长篇分析放进 `content`；项目统一处理 `thinking` 字段及 `</think>` 异常，**不会拿分析过程充当最终答案**。

### 第二步：向量知识库建库

```powershell
python main.py index
```

程序读取 `docs/` 中的 3 份制度，按章节、条款分块，并分批请求本地 BGE-M3，得到真实 1024 维向量。每个片段携带文件名、制度标题、章节、条款、是否模拟等元数据，持久化保存在 `chroma_db/`。

同名文件重新建库会先生成成功后替换旧索引；换嵌入模型时不要复用旧索引。如果修改分块参数，也要重新运行 index。

### 第三步：真实检索

```powershell
python main.py search "DTI为45%的申请人能直接审批通过吗？"
python main.py search "发现客户数据泄露后多久要首报？"
```

结果包含 `question`、`retrieved_chunks`，每个片段包含文件名、章节、条款、余弦相似度和引用原文。可以针对 `SIMILARITY_THRESHOLD`、`TOP_K` 做调参实验。

### 第四步：真实 RAG 问答

```powershell
python main.py ask "关键业务连续中断5小时属于哪一级事件？"
python main.py ask "DTI为45%的申请人能直接审批通过吗？"
```

只将召回且通过相似度门槛的片段传给 Qwen3，并要求以 `[1]`、`[2]` 等标注引用。无相关片段时明确拒答，不依赖模型编造。

### 第五步：实际检索评估

```powershell
python main.py eval
pytest -q
```

按 `tests/golden_questions.json` 中的 6 道真实检索题计算来源命中率 Hit Rate@K 并导出 `retrieval_eval.csv`；**运行前不预设评估分数**。这个指标是“正确文件是否进入 Top-K”，不等同于生成答案的准确率。

### 第六步：可选 HTTP 演示

```powershell
python -m uvicorn api:app --host 127.0.0.1 --port 8000
```

浏览器访问 `http://127.0.0.1:8000/docs` 测试 `POST /ask`，可截图 Swagger 演示。这个本机接口没有用户鉴权，请勿使用 `--host 0.0.0.0` 暴露到其他机器。

## 4. 课件对应与评分要求

| 课件要求 | 交付位置 |
| --- | --- |
| ≥3 份文档，文档清洗切块 | `docs/`、`doc_loader.py` |
| ChromaDB、Embedding | `vectorize_and_store.py`、`ollama_client.py` |
| `retrieve()` + `ask()` | `rag_qa.py` |
| 可溯源、结构化返回 | `source`/`chapter`/`article`/`retrieved_chunks` |
| 模块化、注释、README | 各模块及本文件 |
| API 层分离 | `api.py` |
| 测试/评估加分 | `tests/`、`python main.py eval` |
| 运行截图 | `evidence/README.md` 要求自行真实截图 |

## 5. 常见问题

- **Windows 解析不了 `.lzcapp` 主机名**：它是懒猫内部服务域名。Windows 请用已验证的局域网地址。
- **模型对话有 800 多个 Token 的自言自语**：部分版本的 Qwen3 把推理泄露到 `message.content`，项目已做修复，不代表请求被截断。
- **`Connection refused` / `timed out`**：先通过 `curl.exe http://ollamaURL/api/tags` 检查局域网访问。
- **向量维度不是 1024**：确认 `bge-m3:latest` 已安装、模型名称未错，注意换模型需重建索引。
- **推理很慢**：懒猫微服是模型真正运行的设备，Windows RX 7800 XT 不会自动替微服承担模型推理。
- **不同机器分词、Python 依赖版本不同**：先确保安装 `requirements.txt`，再按实际错误定位；不要伪造成功截图。

## 6. 已知边界

这是学习版工程，使用纯语义检索，不是完整 Graph RAG；PDF 需另装 `pypdf` 且仅支持可提取文字的 PDF，扫描版需独立 OCR。本项目的阈值属于实验起始值，应通过真实结果调参。ChromaDB 数据库是本地文件，不能直接充当企业级权限系统。所有未显示的评估成绩需要运行后才能确定。
