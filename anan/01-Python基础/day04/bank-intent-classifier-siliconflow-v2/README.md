# 银行客户服务智能意图分类引擎

本项目是 AI 开发 / Vibe Coding 课程实战作业：使用 **SiliconFlow API + DeepSeek**，将银行客户咨询识别为 5 类意图，并以 JSON Mode 输出结构化结果。

## 1. 五类意图

| intent | 中文名称 |
|---|---|
| `account_inquiry` | 账户查询类 |
| `transfer_remittance` | 转账汇款类 |
| `credit_card_service` | 信用卡服务类 |
| `loan_inquiry` | 贷款咨询类 |
| `investment_wealth` | 投资理财类 |

课程提供的 5 份测试集已放在 `data/`：

- 账户查询类：500 条
- 转账汇款类：550 条
- 信用卡服务类：600 条
- 贷款咨询类：650 条
- 投资理财类：700 条
- 合计：3000 条

每条测试数据包含：`text`、`intent`、`intent_name`、`id`、`is_multi_intent`。

## 2. 功能覆盖

### 基础要求

- 调用 SiliconFlow 的 DeepSeek 模型
- 强制使用 `response_format={"type": "json_object"}`
- 输出 `intent`、`confidence`、`need_human`、`summary`
- Pydantic 对输出字段进行二次校验

### 进阶要求

- CLI 参数直接传入客户消息
- API Key 使用 `.env` / 环境变量，不写入源码
- 区分鉴权、限流、超时、网络、服务端、JSON 格式等异常
- 指数退避重试
- 模块化代码结构与注释

### 挑战要求

- `evaluation.py` 批量读取本地 JSON 测试集
- 汇总模型预测结果并导出老师规定的三列标准 CSV
- CSV 列固定为：`输入`、`输出`、`置信度分数`
- 自动计算总体和各类别准确率（仅终端显示，不混入提交 CSV）
- `--multi` 支持单条消息多意图拆解
- pytest 单元测试覆盖核心解析、校验和课程测试集完整性

## 3. 安装

建议 Python 3.10+。

```bash
python -m venv .venv
```

Windows PowerShell：

```powershell
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

macOS / Linux：

```bash
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

然后编辑 `.env`：

```env
SILICONFLOW_API_KEY=你的真实APIKey
SILICONFLOW_BASE_URL=https://api.siliconflow.cn/v1
SILICONFLOW_MODEL=deepseek-ai/DeepSeek-V3.2
```

> `.env` 已写入 `.gitignore`，不要把真实密钥提交到作业仓库。

## 4. 单条消息分类

```bash
python main.py "我想查一下工资卡余额"
```

示例输出：

```json
{
  "intent": "account_inquiry",
  "confidence": 0.98,
  "need_human": false,
  "summary": "用户希望查询工资卡余额。"
}
```

另一个例子：

```bash
python main.py "跨行转账失败了怎么办"
```

## 5. 多意图挑战模式

```bash
python main.py "我想查工资卡余额，另外信用卡账单也想看一下" --multi
```

挑战模式会在课程四字段之外增加 `all_intents`：

```json
{
  "intent": "account_inquiry",
  "confidence": 0.95,
  "need_human": false,
  "summary": "用户同时咨询工资卡余额和信用卡账单。",
  "all_intents": [
    "account_inquiry",
    "credit_card_service"
  ]
}
```

## 6. 批量测试与 CSV 导出

### 先做小规模联调

不要第一次就跑全部 3000 条。建议每个类别先测 5 条：

```bash
python evaluation.py --limit-per-file 5
```

共调用 25 次 API，结果输出到：

```text
output/result.csv
```

如果要生成与你这次目标示例一致的 **100 条结果（5 类 × 每类 20 条）**：

```bash
python evaluation.py --limit-per-file 20
```

如需每类随机抽 20 条：

```bash
python evaluation.py --limit-per-file 20 --shuffle --seed 42
```

### 全量评估

```bash
python evaluation.py
```

这会对全部 3000 条数据逐条调用 API，请注意：

- 会产生 API Token 费用；
- 运行时间可能较长；
- 可能触发 RPM/TPM 限流。

必要时可以增加调用间隔：

```bash
python evaluation.py --delay 0.2
```

最终提交 CSV **严格只有 3 列**：

| 列名 | 内容 |
|---|---|
| `输入` | 原始客户咨询文本 |
| `输出` | 模型识别出的中文意图类别，如 `账户查询类` |
| `置信度分数` | 模型返回的 `confidence`，范围 0~1 |

示例：

```csv
输入,输出,置信度分数
急！查询账户开户信息,账户查询类,0.95
在吗？帮我看看活期账户还有多少钱！,账户查询类,0.95
```

> `expected_intent`、`correct`、`summary`、`error` 等调试/评估字段仍会用于程序内部统计，但不会再写入提交 CSV。这样生成的文件就与最新作业模板一致。

## 7. 运行单元测试

```bash
pytest -q
```

## 8. 项目结构

```text
bank-intent-classifier-siliconflow/
├── main.py                 # CLI 单条分类入口
├── classifier.py           # SiliconFlow API 调用、重试、JSON 解析
├── prompts.py              # 五分类 Prompt 与分类边界
├── models.py               # Pydantic Schema
├── config.py               # .env / 环境变量配置
├── evaluation.py           # 批量测试、准确率、三列提交 CSV 导出
├── data/                   # 课程提供的 5 份测试集
├── tests/                  # 单元测试
├── output/                 # 评估输出目录
├── .env.example
├── .gitignore
├── requirements.txt
└── README.md
```
