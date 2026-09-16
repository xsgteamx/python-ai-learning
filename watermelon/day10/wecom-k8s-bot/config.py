# -*- coding: utf-8 -*-
"""
全局配置：所有敏感配置优先从环境变量 / .env 读取。
使用方法：复制 .env.example 为 .env 并填写，或直接设置系统环境变量。
"""
import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

# ================= 企业微信（两种接入模式二选一） =================
# WECOM_MODE:
#   app   = 企业自建应用（需要 CorpID/Secret/AgentId，管理员配置）
#   aibot = 智能机器人（普通成员即可创建，无需 CorpID/Secret/AgentId/可信IP，
#           回复通过回调携带的 response_url 主动发送）
WECOM_MODE = os.environ.get("WECOM_MODE", "app").strip().lower()

# 智能机器人回复消息类型：markdown（推荐，渲染更好）或 text
AIBOT_REPLY_TYPE = os.environ.get("AIBOT_REPLY_TYPE", "markdown").strip().lower()

# ---- 长连接模式（WECOM_MODE=aibot_ws，内网部署用）----
# 在智能机器人 API 设置页选择「长连接」后，页面会给出 BotID 与 Secret（与 URL 回调的 Token/AESKey 不同）
WECOM_WS_BOT_ID = os.environ.get("WECOM_WS_BOT_ID", "")
WECOM_WS_SECRET = os.environ.get("WECOM_WS_SECRET", "")
WECOM_WS_URL = os.environ.get("WECOM_WS_URL", "wss://openws.work.weixin.qq.com")
WECOM_WS_HEARTBEAT = int(os.environ.get("WECOM_WS_HEARTBEAT", "30"))  # 心跳间隔秒数

WECOM_CORP_ID = os.environ.get("WECOM_CORP_ID", "")              # 企业ID（CorpID），app 模式必填
WECOM_AGENT_ID = os.environ.get("WECOM_AGENT_ID", "")            # 应用 AgentId
WECOM_SECRET = os.environ.get("WECOM_SECRET", "")                # 应用 Secret
WECOM_TOKEN = os.environ.get("WECOM_TOKEN", "")                  # 接收消息服务器 Token
WECOM_ENCODING_AES_KEY = os.environ.get("WECOM_ENCODING_AES_KEY", "")  # EncodingAESKey（43位）

# 回调路径与监听地址
WECOM_CALLBACK_PATH = os.environ.get("WECOM_CALLBACK_PATH", "/wecom")
SERVER_HOST = os.environ.get("WECOM_SERVER_HOST", "0.0.0.0")
SERVER_PORT = int(os.environ.get("WECOM_SERVER_PORT", "8080"))

# ================= Kubernetes =================
# kubeconfig 路径；留空则使用默认位置(~/.kube/config)；部署在集群内可自动使用 in-cluster 配置
KUBECONFIG = os.environ.get("KUBECONFIG", None) or None

K8S_LOG_DEFAULT_LINES = int(os.environ.get("K8S_LOG_DEFAULT_LINES", "60"))  # 日志默认行数（约可装进企微消息直接显示）
K8S_LOG_MAX_LINES = int(os.environ.get("K8S_LOG_MAX_LINES", "2000"))         # 日志最大行数

# ================= 消息体限制 =================
# 企业微信智能机器人主动回复 markdown 上限 20480 字节（text 类型上限 2048 字节，若 AIBOT_REPLY_TYPE=text 请自行调小）；日志类内容超限会自动落盘到服务器 logs/ 目录
MAX_REPLY_BYTES = int(os.environ.get("MAX_REPLY_BYTES", "20480"))

# ================= 日志下载 =================
# 超长日志落盘后的下载地址前缀（公网可达的服务器域名，如 https://test1.btxnetwork.com）。
# 留空则只返回服务器本地路径，不提供下载链接。访问路径固定为 {BASE}/wecom-k8s-bot-logs/<文件名>
LOG_DOWNLOAD_BASE_URL = os.environ.get("LOG_DOWNLOAD_BASE_URL", "").rstrip("/")
