# 企业微信 K8s 查询机器人（wecom-k8s-bot）

在企业微信里 **@机器人** 发一句自然语言，机器人自动查询 Kubernetes 并把结果回复到群里/单聊。支持节点资源占用、Pod 日志、Pod 详情、Deployment 日志、崩溃排查、集群巡检等，并可选接入大模型做**意图路由**和**AI 分析**。

```text
成员在企业微信 @机器人（URL 回调 / 长连接）
        │
        ▼
意图解析（LLM 路由，失败自动降级内置规则）
        │
        ▼
查询 Kubernetes（kubeconfig / in-cluster）
        │
        ▼
AI 增强（可选）→ 文本/markdown 回复
        │
        ▼
单聊/群聊收到结果 ｜ 定时巡检推送到企微群
```

## 功能一览

| 能力 | 说明 | 示例 |
| --- | --- | --- |
| 节点资源占用 | 各节点 CPU/内存分配与用量、Top Pod | `查看节点资源占用` / `top node` |
| Pod 日志 | 默认最近 60 行；超长自动保存并提供下载链接 | `查看 nginx-abc-123 的日志` |
| 日志筛选 | 只看错误 / 按关键词过滤（**大小写不敏感**） | `查看 xxx 的错误日志`、`xxx 的日志 包含 INFO` |
| Deployment 日志 | 控制器下所有副本日志混合输出 | `查看 deployment coffee-mp 的日志` |
| Pod 详情 | 状态、事件、退出/重启原因（describe） | `查看 coffee-mp-xxx 的详情` |
| 上次退出原因 | 专门查 Pod 上次退出/重启的原因与退出码 | `查看 coffee-mp-xxx 的上次退出原因` |
| 命名空间 / Pod 列表 | 指定命名空间下的 Pod | `看看有哪些命名空间`、`查看 kube-system 的 pod` |
| 崩溃排查 | 自动定位异常 Pod 并分析错误日志 | `看看哪些 pod 崩溃了` |
| 集群巡检 | 扫描异常 Pod，分析日志/事件/控制器/节点资源，给原因与建议 | `巡检` / `体检` |
| 定时巡检 | 每天固定时间自动巡检并推送到企微群，完整报告含全部 Pod 明细 | 配置 `INSPECT_HOUR` + 群 Webhook |
| 通用只读查询 | LLM 自主识别未预定义的问题，白名单内 20+ 资源均可查 | `看看有哪几个 ingress` 等 |
| 安全拦截 | 重启/删除等**变更操作一律拒绝**，只读不写 | 发 `重启 xxx` → 提示无法执行 |

其他体验优化：

- **AI 增强**：配了模型 Key 后，查询结果自动附上大模型的异常解读/原因/建议
- **相似 Pod 引导**：Pod 名输错时给出相近名称提示
- **耗时提示**：查询超过阈值显示 `查询耗时 X.X 秒`
- **手机端优化**：错误/事件高亮、列表状态标色、行截断，重点突出
- **运行日志**：程序日志按天切割写入 `/app/logs/bot.log`，保留 7 天

---

## 接入模式（三选一）

| 模式 | 适用场景 | 需要什么 | 说明 |
| --- | --- | --- | --- |
| **A. 智能机器人 URL 回调**（推荐） | 有公网 HTTPS 域名 | 机器人页面给的 `Token` + `EncodingAESKey` | **无需企微管理员**，工作台自己创建即可 |
| B. 智能机器人 长连接 | 内网服务器，无公网地址 | 机器人页面给的 `BotID` + `Secret` | 服务端主动连 wss，需能出网 |
| C. 自建应用 | 有企微管理员权限 | `CorpID` / `AgentId` / `Secret` + Token/AESKey | 双向通信，需配置可信 IP、可见范围 |

> 智能机器人（API 模式）不需要 CorpID/AgentId/Secret，也绕开可信 IP；URL 回调模式下回复走回调携带的 `response_url`，**每回调只能回复一次**。

### 方案 A：智能机器人 URL 回调（推荐，无需管理员）

1. 企业微信客户端 → **工作台 → 智能机器人 → 创建机器人 → 手动创建 → API 模式**。
2. 连接方式选 **URL 回调**，填写：
   - **回调地址**：`https://你的域名/wecom`
   - **Token / EncodingAESKey**：页面生成或自填，点保存触发 URL 验证（服务已启动即可通过）
3. 服务器 `.env`（只需三个值，其余可留空）：
   ```env
   WECOM_MODE=aibot
   WECOM_TOKEN=<机器人页面里的Token>
   WECOM_ENCODING_AES_KEY=<机器人页面里的EncodingAESKey>
   ```
4. 部署后测试：单聊机器人发 `帮助`，或群里 @机器人 发 `帮我查看k8s集群资源占用情况`。

说明：

- 回复格式默认 **markdown**（渲染更好）；纯文本在 `.env` 加 `AIBOT_REPLY_TYPE=text`
- 若保存回调提示“域名不可信”，需要管理员把域名/IP 加白名单，或改用长连接模式

### 方案 B：智能机器人 长连接（内网部署）

1. 机器人 API 设置页连接方式选 **长连接**，页面给出 `BotID` 与长连接专用 `Secret`（与 URL 回调的 Token/AESKey 不是同一套，两种方式二选一）。
2. 服务器 `.env`：
   ```env
   WECOM_MODE=aibot_ws
   WECOM_WS_BOT_ID=<BotID>
   WECOM_WS_SECRET=<长连接专用Secret>
   ```
3. 启动长连接服务：
   ```bash
   docker compose up -d --build wecom-k8s-bot-ws
   docker logs -f wecom-k8s-bot-ws   # 看到"连接建立成功"即 OK
   ```
4. 内网服务器需能出网访问 `wss://openws.work.weixin.qq.com:443`；同一机器人同一时间只能有一个长连接，勿与 URL 回调同时配。

### 方案 C：自建应用（需要管理员）

1. 企微管理后台 → 应用管理 → 创建**自建应用**，记录 `CorpID`、`AgentId`、`Secret`。
2. 应用详情 → 接收消息 → 设置 API 接收：URL 填 `https://你的域名/wecom`，Token / EncodingAESKey 随机获取并保存（保存时会向 URL 发验证请求）。
3. 设置可见范围（建议根部门）、把服务器公网 IP 加入**企业可信 IP**（否则调用发送 API 报 60020）。
4. 服务器 `.env`：
   ```env
   WECOM_MODE=app
   WECOM_CORP_ID=ww...
   WECOM_AGENT_ID=1000002
   WECOM_SECRET=...
   WECOM_TOKEN=...
   WECOM_ENCODING_AES_KEY=...
   ```

---

## Docker 部署（公网服务器）

```bash
# 1) 拉取代码并进入目录
git clone git@gitee.com:xzyi1/wecom-k8s-bot.git && cd wecom-k8s-bot
git checkout dev

# 2) 配置
cp .env.example .env          # 填写接入模式与集群相关配置（见上）
mkdir -p ~/.kube
scp ~/.kube/config root@服务器IP:/root/.kube/config   # 服务器需能访问集群 API Server

# 3) 启动
docker compose up -d --build

# 4) 验证
curl http://127.0.0.1:8080/health     # -> ok
docker compose logs -f wecom-k8s-bot
```

说明：

- kubeconfig 以只读卷挂载进容器（`/root/.kube/config`），路径不对改 `docker-compose.yml` 里的挂载源；若部署在集群 Pod 内会自动使用 in-cluster 配置，无需挂载
- 容器时区已设为 `Asia/Shanghai`
- `logs/` 目录挂载进容器 `/app/logs`，用于存放：超长日志下载文件、巡检报告、运行日志 `bot.log`
- 生产启动方式：`gunicorn -w 2 --threads 4 app:app`（巡检后台线程在**模块顶层**启动，gunicorn 同样生效；多 worker 用文件锁保证只有一个调度）

### HTTPS 回调与日志下载（nginx）

企微回调要求公网 HTTPS，同时给超长日志/巡检报告提供下载。推荐 Caddy（自动证书）或 nginx + certbot：

```nginx
server {
    listen 443 ssl;
    server_name your.domain.com;
    ssl_certificate     /etc/letsencrypt/live/your.domain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/your.domain.com/privkey.pem;
    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
    # 日志下载目录：/wecom-k8s-bot-logs/ -> 容器 /app/logs（宿主机项目 logs/）
    location /wecom-k8s-bot-logs/ {
        alias /root/wecom-k8s-bot/logs/;
    }
}
```

在 `.env` 配置 `LOG_DOWNLOAD_BASE_URL=https://your.domain.com` 后，超长日志和巡检完整报告会自动给出 `https://your.domain.com/wecom-k8s-bot-logs/<文件名>` 下载链接。

### 服务器一键自检

```bash
docker exec wecom-k8s-bot python check_server.py
# 验证发送链路（会发一条测试消息）：--touser 你的userid
docker exec wecom-k8s-bot python check_server.py --touser xxx
```

---

## 配置项（.env）

```env
# ===== 接入模式 =====
WECOM_MODE=aibot            # app=自建应用 | aibot=智能机器人URL回调(推荐) | aibot_ws=长连接(内网)
AIBOT_REPLY_TYPE=markdown   # markdown / text

# 自建应用（模式 app）
WECOM_CORP_ID=ww...
WECOM_AGENT_ID=1000002
WECOM_SECRET=...
WECOM_TOKEN=...             # API接收 里的 Token
WECOM_ENCODING_AES_KEY=...  # API接收 里的 EncodingAESKey（43位）

# 长连接（模式 aibot_ws）
# WECOM_WS_BOT_ID=...
# WECOM_WS_SECRET=...

# ===== 服务监听 =====
# WECOM_SERVER_HOST=0.0.0.0
# WECOM_SERVER_PORT=8080
# WECOM_CALLBACK_PATH=/wecom

# ===== Kubernetes =====
# KUBECONFIG=C:/Users/you/.kube/config     # 留空用 ~/.kube/config，集群内自动 in-cluster
# LOG_DOWNLOAD_BASE_URL=https://your.domain.com   # 超长日志/巡检报告下载链接前缀
# K8S_LOG_DEFAULT_LINES=60                  # 日志默认行数
# K8S_LOG_MAX_LINES=2000                    # 单次最大行数

# ===== 消息体 =====
# MAX_REPLY_BYTES=20480     # 企微 markdown 上限；text 模式需 ≤2048

# ===== 定时巡检（可选） =====
# WECOM_GROUP_WEBHOOK=https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx  # 群机器人
# INSPECT_ENABLED=1         # 置 0 关闭
# INSPECT_HOUR=9            # 每天推送时间（北京时间）
# INSPECT_MINUTE=0
# 注意：群机器人 Webhook 只负责单向推送到群（定时报告）；与智能机器人(URL回调/长连接)是两套东西

# ===== LLM 意图路由 + AI 分析（可选，阿里云百炼） =====
# LLM_API_KEY=sk-...        # 到阿里云百炼(DashScope)申请，有免费额度；配了即启用
# LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
# LLM_MODEL=qwen-plus       # 或 qwen-turbo（更便宜）
# LLM_TIMEOUT=8
# LLM_ENHANCE=1             # 查询结果自动附 AI 分析；置 0 只做意图路由

# ===== 体验 =====
# QUERY_COST_SHOW_SEC=1.0           # 回复显示"查询耗时 X 秒"的最小耗时
# THINKING_THRESHOLD_SEC=1.2        # 长连接模式：超时先发"正在思考"提示
```

---

## 定时集群巡检

巡检已**内置在主服务**（`app.py` 后台线程），无需独立容器。到点自动：

1. 扫描全部命名空间的 Pod，标记异常（Pending/Failed/CrashLoopBackOff/退出码非 0）
2. 对异常 Pod 做深度分析：日志、事件、Deployment 变更、节点资源占用
3. 生成两份报告：
   - **群消息版**（精简）：状态总览 + 节点资源 + 异常分析 + 原因与建议
   - **完整报告**（落盘 `logs/cluster-inspect-*.txt`）：额外含**全部 Pod 明细**（`ns/name [状态] 就绪x/y 重启n次`，异常标 ⚠️），群消息附下载链接
4. AI 增强（配了 Key 时）后推送到企微群

配置：

```env
INSPECT_ENABLED=1
INSPECT_HOUR=9
INSPECT_MINUTE=0
WECOM_GROUP_WEBHOOK=https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx
```

验证：

```bash
docker logs -f wecom-k8s-bot   # 应看到：定时巡检已内置启动 / 下次巡检推送: ...（N 秒后）
```

> 手动触发：群里发 `巡检` / `体检`；独立跑一次：`docker exec wecom-k8s-bot python inspect_scheduler.py --once`。

---

## 运行日志

- 程序运行日志按天切割写入 `/app/logs/bot.log`（`bot.log.2026-09-20` 为历史文件），保留 7 天自动滚动
- `docker logs` 与文件双通道并存；容器日志另有 10MB×3 轮转
- 宿主机直接查看：`tail -f logs/bot.log`

---

## 本地开发与测试

```bash
python -m venv .venv && .venv\Scripts\activate   # Windows；Linux: source .venv/bin/activate
pip install -r requirements.txt

python app.py                # 开发启动（0.0.0.0:8080，/wecom）

# 测试（不需要真实企微/集群）
python selftest.py           # 意图解析自测（39 用例）
python test_llm_router.py    # LLM 路由降级测试
python test_aibot.py         # URL 回调模式集成测试
python test_aibot_ws.py      # 长连接模式集成测试
```

> 测试文件（`selftest.py`、`test_*.py`）仅供本地使用，**不进入 git 仓库**；提交代码时仅提交主程序文件。

## 安全说明

- 所有查询均为**只读**操作；`kubectl` 侧未授予写权限
- 对重启/删除/扩容等**变更指令一律拒绝**并提示，防止误操作
- LLM 仅用于意图解析与结果分析，**不会**生成或执行任何变更命令
- 接入层面：URL 回调有签名与 AES 加密校验；下载目录只暴露 `logs/` 下的文件

## 常见问题

| 现象 | 原因 / 处理 |
| --- | --- |
| 保存回调配置时 URL 验证失败 | Token/AESKey 与 `.env` 不一致；URL 必须公网 HTTPS 可达；服务未启动 |
| 收到消息但不回复 | 看 `docker logs` 中 `cmd=` 与报错；确认机器人 API 模式（aibot）而非群机器人 |
| 定时巡检没生效 | 看日志是否有“定时巡检已内置启动”；`INSPECT_MINUTE` 改后需重启容器；没配群 Webhook 只生成报告不推送 |
| 回复太长被截断 | 超长日志自动保存到 `logs/` 并给下载链接（需配 `LOG_DOWNLOAD_BASE_URL` + nginx 映射） |
| 查日志提示 Pod 不存在 | 名称拼写或命名空间不对；会给出相似 Pod 名引导 |
| `info` 查不到但 `INFO` 可以 | 已修复：关键词过滤**大小写不敏感**，新旧版本确认已更新 |
| 查询耗时偏长 | 大集群首次全量扫描属正常；可调低 `K8S_LOG_MAX_LINES` |
| LLM 未生效 | 确认 `LLM_API_KEY` 已填并重启；网络不通/超时会自动降级到内置规则，功能不受影响 |

## 扩展新查询

- **意图解析**：`intent.py` 加规则（正则/关键词）
- **查询实现**：`k8s_api.py` 写对应的只读查询函数
- **路由与参数校验**：`llm_router.py` 的 `ALLOWED_CMDS` / `_sanitize_params`
- **回复组装**：`replies.py` 的 `build_reply` 加分发分支；需要 AI 增强时加入 `_AI_ENHANCE_CMDS`

新增能力记得同步更新机器人回复的「帮助」文本（`intent.py` 的 `HELP_TEXT`）。

## 项目结构

```
wecom-k8s-bot/
├── app.py               # Flask 主服务：回调 + 回复 + 内置巡检线程（模块顶层启动）
├── aibot_ws.py          # 智能机器人长连接服务（内网模式）
├── intent.py            # 自然语言意图解析（正则 + 命令路由 + 变更操作拦截）
├── llm_router.py        # LLM 意图路由（阿里云百炼，失败自动降级内置规则）
├── replies.py           # 回复组装 + AI 增强
├── k8s_api.py           # Kubernetes 全部只读查询（资源/日志/详情/巡检）
├── inspect_scheduler.py # 定时巡检调度（--once 可单独执行）
├── wecom_api.py         # access_token 管理 + 发送消息 API
├── wx_crypt.py          # 企微消息加解密（与官方 WXBizMsgCrypt 兼容）
├── config.py            # 配置（读取环境变量 / .env）
├── check_server.py      # 服务器一键自检
├── Dockerfile           # python:3.12-slim + gunicorn
├── docker-compose.yml   # 主服务 + 长连接服务
├── logs/                # 超长日志 / 巡检报告 / 运行日志 bot.log（容器内 /app/logs）
└── .env.example         # 配置模板
```
