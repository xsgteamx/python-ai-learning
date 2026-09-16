# 企业微信 K8s 查询机器人

在企业微信里 **@机器人** 发送「帮我查看k8s集群资源占用情况」「查看某个 pod 的日志」等指令，机器人自动查询 Kubernetes 并把结果回复到群里（或单聊）。

## 工作原理

采用企业微信 **自建应用** 模式（双向通信）：

```
成员在企业微信 @应用/发消息
        │ 企业微信后台
        ▼
  HTTPS POST（加密 XML）→ 你的服务 {/wecom}
        │ 校验签名 + AES 解密
        ▼
  意图解析（资源占用 / Pod日志 / Pod列表）
        │ 查询 Kubernetes（kubeconfig / in-cluster）
        ▼
  组装文本 → 调用企业微信 API 主动回复
        │
        ▼
  群聊：appchat/send（失败自动私发）｜单聊：message/send
```

> 为什么不直接用“群机器人(Webhook)”？Webhook 机器人只能**单向推送**，收不到成员消息，无法实现“@ 后回复”。自建应用支持双向通信，是官方推荐做法。

## 项目结构

```
wecom-k8s-bot/
├── app.py            # Flask 主服务（回调 + 异步处理）
├── wx_crypt.py       # 企业微信消息加解密（与官方 WXBizMsgCrypt 兼容）
├── wecom_api.py      # access_token 管理 + 发送消息 API
├── k8s_api.py        # Kubernetes 查询（资源/日志/Pod列表）
├── intent.py         # 自然语言意图解析
├── config.py         # 配置（读取环境变量 / .env）
├── selftest.py       # 自测脚本（加解密官方向量 + 意图解析 + 可选k8s连通性）
├── test_server.py    # 服务端集成测试（模拟企微 URL 验证 / 加密消息回调 / 群聊消息）
├── check_server.py   # 服务器端一键自检（配置/企微API/可信IP/k8s/发测试消息）
├── Dockerfile        # 生产镜像（python:3.12-slim + gunicorn）
├── docker-compose.yml# 一键部署（env_file + kubeconfig 挂载 + 健康检查）
├── .dockerignore
├── requirements.txt
├── .env.example      # 配置模板（复制为 .env 填写）
└── README.md
```

## 快速开始

### 方案三（内网部署·无需公网地址）：智能机器人长连接

如果你的服务器在**内网**（没有公网 IP/域名，企业微信无法回调进来），用这个方案——服务端主动连企业微信的 WebSocket 长连接：

1. 机器人 API 设置页，连接方式选 **长连接**，页面会给出 **BotID** 和 **Secret**（长连接专用，与 URL 回调的 Token/AESKey 不同；**切换后 URL 回调会失效**，两种方式二选一）。
2. 服务器 `.env`：
   ```
   WECOM_MODE=aibot_ws
   WECOM_WS_BOT_ID=<机器人页面里的BotID>
   WECOM_WS_SECRET=<长连接专用Secret>
   ```
3. 启动长连接服务（复用同一镜像）：
   ```bash
   docker compose up -d --build wecom-k8s-bot-ws
   docker logs -f wecom-k8s-bot-ws   # 看到"连接建立成功，已发送订阅"即 OK
   ```
4. 测试：单聊机器人发「帮助」，或群里 @机器人 发「帮我查看k8s集群资源占用情况」。

说明：
- 内网服务器**必须能出网**访问 `wss://openws.work.weixin.qq.com:443`（普通内网一般可以；完全隔离的内网不行）。
- 消息为明文 JSON，**无需加解密**；回复用 `aibot_respond_msg`（透传 req_id，支持 markdown）。
- 同一机器人**同一时间只能有一个长连接**（新连接会踢旧连接）。只在部署长连接的一台机器上运行 `wecom-k8s-bot-ws`，不要多开；也别让它与 URL 回调模式同时配。
- 程序自带 30 秒应用层心跳 + 断线指数退避自动重连；容器 `restart: unless-stopped` 兜底。

### 方案二（推荐·无需管理员权限）：智能机器人

如果你**没有企微管理员权限**（无法创建自建应用），用这个方案——在客户端自己创建一个 **API 模式的智能机器人**，完全绕开 CorpID/Secret/AgentId/可信IP/可见范围：

1. 企业微信客户端 → **工作台 → 智能机器人 → 创建机器人 → 手动创建 → API 模式**。
2. 连接方式选 **URL 回调**，填写：
   - **回调地址**：`https://你的域名/wecom`（复用现有服务，无需改代码）
   - **Token / EncodingAESKey**：页面生成（或自填），点保存触发 URL 验证。
3. 服务器 `.env` 改为（**只需三个值**，其余可留空）：
   ```
   WECOM_MODE=aibot
   WECOM_TOKEN=机器人页面里的Token
   WECOM_ENCODING_AES_KEY=机器人页面里的EncodingAESKey
   ```
4. 重建重启：`docker compose up -d --build`。
5. 测试：单聊机器人发「帮助」，或群里 @机器人 发「帮我查看k8s集群资源占用情况」。

说明：
- 智能机器人回调是加密 JSON，加解密方式与自建应用相同（ReceiveId 为空）；回复通过回调携带的 `response_url` 主动发送（明文 JSON，无需 access_token、无需可信IP）。
- 回复格式默认 **markdown**（渲染更好），如需纯文本在 `.env` 加 `AIBOT_REPLY_TYPE=text`。
- 若保存回调时提示“域名不可信”：URL 回调要求企业可信域名/IP，需要管理员把该域名/IP 加白名单；或改用**长连接**方式（需 BotID/Secret，程序需另行适配）。
- 回调里的 `from.userid` 是加密 userid，不影响回复功能。

### 方案一：自建应用（需要管理员权限）

### 1. 企业微信后台配置（一次性）

1. 登录 [企业微信管理后台](https://work.weixin.qq.com/wework_admin/frame) → 「应用管理」→「应用」→「创建应用」，创建一个**自建应用**。
2. 记录三个值：`CorpID`（我的企业 → 企业信息）、应用详情页的 `AgentId` 和 `Secret`。
3. 进入应用详情 →「接收消息」→「设置API接收」：
   - **URL**：填你的公网地址，如 `https://your-domain.com/wecom`（必填 HTTPS，可用 nginx + 证书，或 frp/ngrok 临时映射）。
   - **Token**：点“随机获取”生成，保存。
   - **EncodingAESKey**：点“随机获取”生成（43 位），保存。
   - 点“保存”时会向该 URL 发一次 GET 验证请求，服务跑起来后即可通过。
4. 应用可见范围：建议设为**根部门**或至少包含使用成员（群聊回复 appchat/send 要求可见范围为根部门；若受限，程序会自动改为私发回复，不阻塞使用）。
5. 「企业可信IP」：把部署服务器的**公网 IP** 加入白名单（否则调用发送 API 会报 `60020` 等错误）。

### 2. 部署服务（本机演示 / 服务器）

```bash
cd wecom-k8s-bot
python -m venv .venv
# Windows: .venv\Scripts\activate    Linux/Mac: source .venv/bin/activate
pip install -r requirements.txt

copy .env.example .env        # Windows
# cp .env.example .env        # Linux/Mac，然后填写真实配置

python app.py                 # 监听 0.0.0.0:8080，回调路径 /wecom
```

K8s 侧：运行机器上需能访问集群（`kubectl get nodes` 可通即可）；部署在集群 Pod 内会自动使用 in-cluster 配置。

### 2.5 Docker 部署（公网服务器，推荐）

项目已内置 `Dockerfile` 与 `docker-compose.yml`，一行命令启动，自动跑 gunicorn（生产 WSGI）。

```bash
# 1) 把项目上传到服务器（git clone 或 scp 目录），进入项目目录
# 2) 完整填写 .env（WECOM_CORP_ID / WECOM_AGENT_ID / WECOM_SECRET 以及已有的 Token / EncodingAESKey）
# 3) 把本机 kubeconfig 放到服务器（服务器需能访问集群 API Server）：
#    scp ~/.kube/config root@服务器IP:/root/.kube/config
# 4) 构建并启动
docker compose up -d --build
# 5) 验证
curl http://127.0.0.1:8080/health     # -> ok
docker compose logs -f wecom-k8s-bot  # 查看日志
```

说明：
- 容器时区已设为 `Asia/Shanghai`，报表时间戳为本地时间。
- kubeconfig 以只读卷挂载进容器（`/root/.kube/config`），路径不匹配时改 `docker-compose.yml` 里的挂载源。
- 若希望**部署在集群内部**（Pod 内），无需挂载 kubeconfig，程序会自动使用 in-cluster 配置。
- 停止/重启：`docker compose down` / `docker compose restart`。

#### 服务器一键自检（排障利器）

在服务器上跑一次，逐项告诉你「配置 / Secret与可信IP / k8s连通」哪里不通：

```bash
# 宿主机（项目目录内）：
python3 check_server.py
# 或容器内：
docker exec wecom-k8s-bot python check_server.py

# 想顺便验证“发送消息”链路（会给你发一条测试消息）：
docker exec wecom-k8s-bot python check_server.py --touser 你的userid
# userid 查看：先在企微里 @机器人 发一条消息，再查 docker compose logs 里的 from= 字段
```

失败项会给出原因与处理指引（Secret 错误 / 可信IP未配置 / k8s不通等）。

#### HTTPS 回调（必须）

企微回调 URL 要求公网 HTTPS。推荐用 **Caddy**（自动申请/续期证书，最省事）或 nginx + certbot：

```bash
# Caddyfile（服务器上，将 your.domain.com 换成你的域名）
your.domain.com {
    reverse_proxy 127.0.0.1:8080
}
```

```nginx
# nginx 参考配置（配合 certbot 申请证书后）
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
}
server { listen 80; server_name your.domain.com; return 301 https://$host$request_uri; }
```

然后回到企微后台「设置API接收」，URL 填 `https://your.domain.com/wecom`，Token / EncodingAESKey 与 `.env` 完全一致，保存即验证通过。

> **企业可信IP**：`docker compose` 部署的服务器公网 IP 必须加入企微后台「管理工具 → 企业可信IP」，否则调用发送消息 API 会报 `60020`。
> **域名解析**：服务器需绑定已备案域名并解析到该公网 IP；若无域名，可用 frp/cpolar/ngrok 临时映射 HTTPS。

### 3. 使用

把应用添加到某个群（群设置 → 添加应用/机器人），或在单聊里找到该应用，然后：

| 你说 | 机器人回复 |
| --- | --- |
| @机器人 帮我查看k8s集群资源占用情况 | 节点/Pod 维度 CPU、内存已分配占比，metrics-server 实际用量，Top Pod |
| @机器人 查看 nginx-abc-123 的日志 | 该 Pod 最近 200 行日志（支持模糊匹配、命名空间/名字） |
| @机器人 日志 default/nginx-abc-123 后300行 | 指定命名空间与行数 |
| @机器人 看看有哪些pod | Pod 列表 |
| @机器人 帮助 | 指令说明 |

## 本地自测（无需真实企微/集群）

```bash
python selftest.py            # 加解密官方向量 + 意图解析
python selftest.py --k8s      # 额外验证本机 kubeconfig 与查询输出
python test_server.py         # 模拟企微回调全流程（URL验证/加密消息/群聊消息）
```

## 常见问题

| 现象 | 原因 / 处理 |
| --- | --- |
| 保存回调配置时 URL 验证失败(403) | Token / EncodingAESKey / CorpID 填写不一致；URL 必须公网 HTTPS 可达；服务未启动 |
| 收到消息但一直不回复 | ① 检查日志中 cmd 解析与发送报错；② `access_token` 获取失败多半是企业可信IP未配置；③ 群聊 appchat/send 受限会自动私发 |
| 群聊回复失败 | appchat/send 要求应用可见范围为根部门；程序已自动私发兜底 |
| 资源占用里没有“实际用量” | 集群未安装 metrics-server，属正常，仅展示已分配 requests |
| 日志提示 Pod 不存在 | 名称拼写或命名空间不对；先问“看看有哪些pod” |

## 扩展

- 想加新指令（如查看 Deployment、节点信息）：在 `intent.py` 加规则，在 `app.py` 的 `_build_reply` 里加分支，对应逻辑写在 `k8s_api.py`。
- 生产部署建议：`pip install waitress` 后用 `waitress.serve(app, host=..., port=...)` 替代 Flask 开发服务器；回调域名反代到 8080。
- 日志/资源查询均为只读操作，未引入写权限，安全风险低；如需鉴权可在回调里校验 `FromUserName` 所属部门。
