# -*- coding: utf-8 -*-
"""
企业微信 K8s 查询机器人主服务（Flask）。

流程：
1. 企业微信将成员消息（加密 XML）POST 到 {WECOM_CALLBACK_PATH}
2. 本服务校验签名 -> 解密 -> 解析意图 -> 查询 Kubernetes
3. 通过企业微信 API 主动回复（群聊优先，失败自动私发）

启动：python app.py   （生产建议用 waitress/gunicorn 部署）
"""
import json
import logging
import logging.handlers
import os
import threading
import time
import xml.etree.ElementTree as ET

from flask import Flask, Response, abort, request, send_from_directory

import config
import intent
import k8s_api
from replies import _cost_header, _dedupe, _truncate, build_reply
from wecom_api import WeComApi, WeComApiError
from wx_crypt import WXBizMsgCrypt, WeComCryptoError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("wecom-k8s-bot")


def setup_file_logging():
    """运行日志按天切割写入 /app/logs/bot.log（保留 7 天）；docker logs 仍可见。

    gunicorn 多 worker 各自加载模块，这里防重复添加文件 handler。
    """
    try:
        log_dir = k8s_api.LOG_DIR
        os.makedirs(log_dir, exist_ok=True)
        for h in logging.getLogger().handlers:
            if isinstance(h, logging.handlers.TimedRotatingFileHandler) \
                    and getattr(h, "baseFilename", "").endswith("bot.log"):
                return
        fh = logging.handlers.TimedRotatingFileHandler(
            os.path.join(log_dir, "bot.log"),
            when="midnight", interval=1, backupCount=7, encoding="utf-8",
        )
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        logging.getLogger().addHandler(fh)
        logger.info("运行日志已写入 %s（按天切割，保留 7 天）", os.path.join(log_dir, "bot.log"))
    except Exception as exc:
        logger.warning("运行日志文件保存初始化失败（不影响运行）: %s", exc)


setup_file_logging()

app = Flask(__name__)

# 接入模式：
#   aibot = 智能机器人（ReceiveId 为空字符串，无需 CorpID/Secret）
#   app   = 自建应用（ReceiveId 为 CorpID）
if config.WECOM_MODE == "aibot":
    _crypt = WXBizMsgCrypt(config.WECOM_TOKEN, config.WECOM_ENCODING_AES_KEY, "")
else:
    _crypt = WXBizMsgCrypt(config.WECOM_TOKEN, config.WECOM_ENCODING_AES_KEY, config.WECOM_CORP_ID)
_api = WeComApi(config.WECOM_CORP_ID, config.WECOM_SECRET, config.WECOM_AGENT_ID)


def _build_reply(cmd, params):
    """根据意图执行查询，返回回复文本（转发公共模块，保留旧名引用）。"""
    return build_reply(cmd, params)


def _handle_wecom_message(msg):
    """在工作线程中执行：解析、查询、回复。"""
    msg_type = msg.get("MsgType", "")
    if msg_type != "text":
        logger.info("收到回调消息 MsgType=%s from=%s chat=%s，非文本，忽略", msg_type, msg.get("FromUserName"), msg.get("ChatId"))
        return

    msg_id = msg.get("MsgId")
    if not _dedupe(msg_id):
        return

    content = msg.get("Content", "")
    cmd, params = intent.parse_intent(content)
    logger.info("cmd=%s params=%s from=%s chat=%s", cmd, params, msg.get("FromUserName"), msg.get("ChatId"))

    reply = _truncate(_build_reply(cmd, params), config.MAX_REPLY_BYTES)

    user_id = msg.get("FromUserName", "")
    agent_id = msg.get("AgentID") or config.WECOM_AGENT_ID
    chat_id = msg.get("ChatId", "")

    try:
        if chat_id:
            data = _api.send_to_chat(chat_id, reply)
            if data.get("errcode", -1) != 0:
                logger.warning("appchat/send failed: %s, fallback to private message", data)
                _api.send_to_user(
                    user_id,
                    f"（群聊回复受限，已私发给你，原因为: {data.get('errmsg', '未知')}）\n{reply}",
                    agent_id=agent_id,
                )
        else:
            _api.send_to_user(user_id, reply, agent_id=agent_id)
    except WeComApiError as exc:
        logger.error("send failed: %s", exc)


def _handle_aibot_message(payload):
    """智能机器人回调处理：解析 -> 查询 -> 通过 response_url 主动回复。"""
    msgtype = payload.get("msgtype", "")
    if msgtype != "text":
        logger.info("[aibot] 忽略非文本消息 msgtype=%s", msgtype)
        return

    msg_id = payload.get("msgid")
    if not _dedupe(msg_id):
        return

    content = (payload.get("text") or {}).get("content", "")
    response_url = payload.get("response_url", "")
    cmd, params = intent.parse_intent(content)
    logger.info(
        "[aibot] cmd=%s params=%s chattype=%s chatid=%s",
        cmd, params, payload.get("chattype"), payload.get("chatid"),
    )

    t0 = time.monotonic()
    reply = _truncate(_build_reply(cmd, params), config.MAX_REPLY_BYTES)
    cost = time.monotonic() - t0
    head = _cost_header(cost)
    if head:
        reply = head + reply
    if not response_url:
        logger.warning("[aibot] 回调缺少 response_url，无法主动回复")
        return
    try:
        data = _api.reply_via_response_url(response_url, reply, msgtype=config.AIBOT_REPLY_TYPE)
        logger.info("[aibot] response_url 回复结果: %s", data)
    except Exception as exc:
        logger.error("[aibot] response_url 回复失败: %s", exc)


# ---------------- 回调路由 ----------------
@app.route(config.WECOM_CALLBACK_PATH, methods=["GET", "POST"])
def wecom_callback():
    args = request.args
    msg_signature = args.get("msg_signature", "")
    timestamp = args.get("timestamp", "")
    nonce = args.get("nonce", "")

    if request.method == "GET":
        # 后台保存回调配置时的 URL 验证
        echostr = args.get("echostr", "")
        try:
            ret = _crypt.verify_url(msg_signature, timestamp, nonce, echostr)
        except WeComCryptoError:
            ret = None
        if not ret:
            logger.warning("URL 验证失败(403)：请核对 Token/EncodingAESKey/CorpID 与 .env 是否一致")
            abort(403)
        return Response(ret)

    # POST：解密消息后异步处理，立即返回 success
    body = request.get_data(as_text=True)
    try:
        plain = _crypt.decrypt_msg(msg_signature, timestamp, nonce, body)
    except WeComCryptoError as exc:
        logger.warning("回调解密失败(403)：%s（智能机器人模式请确认 Token/EncodingAESKey 与机器人页面一致）", exc)
        abort(403)
    if not plain:
        logger.warning(
            "回调签名校验失败(403)：请求已到达但被拒绝，请核对 Token/EncodingAESKey 与企业微信后台是否一致。"
            "msg_signature=%s ts=%s nonce=%s body_len=%s",
            msg_signature, timestamp, nonce, len(body),
        )
        abort(403)

    # 自动识别消息格式（防止 WECOM_MODE 漏配也能工作）：
    #   智能机器人明文 = JSON（以 { 开头）；自建应用明文 = XML（以 < 开头）
    plain_stripped = plain.strip()
    if config.WECOM_MODE == "aibot" or plain_stripped.startswith("{"):
        try:
            payload = json.loads(plain_stripped)
        except ValueError:
            logger.warning("智能机器人回调解密内容不是合法 JSON，拒绝")
            abort(403)
        if config.WECOM_MODE != "aibot":
            logger.info("检测到智能机器人 JSON 回调，按智能机器人模式处理（建议在 .env 设置 WECOM_MODE=aibot）")
        threading.Thread(target=_handle_aibot_message, args=(payload,), daemon=True).start()
    elif plain_stripped.startswith("<"):
        try:
            root = ET.fromstring(plain_stripped)
            msg = {child.tag: (child.text or "") for child in root}
        except ET.ParseError:
            logger.warning("xml 解析失败")
            abort(403)
        threading.Thread(target=_handle_wecom_message, args=(msg,), daemon=True).start()
    else:
        logger.warning("回调解密内容既不是 JSON 也不是 XML，拒绝（len=%s）", len(plain_stripped))
        abort(403)
    return Response("success")


@app.route("/health")
def health():
    return Response("ok")


# 定时巡检后台线程的进程级锁（防 gunicorn 多 worker 各自重复启动调度线程）
_INSPECT_LOCK_FD = None


def _start_inspect_scheduler():
    """后台线程内置定时巡检：与问答服务同一进程，无需独立容器。

    每天 INSPECT_HOUR:INSPECT_MINUTE（北京时间）生成巡检报告并推送到企微群。
    在模块顶层调用：无论 flask run 还是 gunicorn(app:app) 都会启动；
    gunicorn 多 worker 场景用文件锁保证只有一个 worker 持有调度线程。
    """
    global _INSPECT_LOCK_FD
    if not config.INSPECT_AUTOSTART:
        logger.info("INSPECT_AUTOSTART=0，定时巡检后台调度未启动")
        return
    try:
        import inspect_scheduler
    except Exception as exc:
        logger.warning("定时巡检模块加载失败，巡检未启用: %s", exc)
        return
    if not config.INSPECT_ENABLED:
        logger.info("INSPECT_ENABLED=0，定时巡检未启用")
        return
    # 文件锁：gunicorn 多 worker 各自加载模块，仅首个 worker 拿到锁启动调度
    try:
        import fcntl
    except ImportError:
        fcntl = None
    if fcntl is not None:
        try:
            os.makedirs(k8s_api.LOG_DIR, exist_ok=True)
            lock_path = os.path.join(k8s_api.LOG_DIR, ".inspect-scheduler.lock")
            _INSPECT_LOCK_FD = open(lock_path, "a+")
            fcntl.flock(_INSPECT_LOCK_FD, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            logger.info("定时巡检调度已在其他 worker 中运行，本 worker 跳过")
            return

    def _runner():
        try:
            inspect_scheduler.run_scheduler()
        except Exception as exc:
            logger.error("定时巡检调度异常退出: %s", exc)

    threading.Thread(target=_runner, name="inspect-scheduler", daemon=True).start()
    logger.info(
        "定时巡检已内置启动：每天 %02d:%02d（北京时间）生成报告并推送到企微群（WECOM_GROUP_WEBHOOK%s）",
        config.INSPECT_HOUR, config.INSPECT_MINUTE,
        "已配置" if config.WECOM_GROUP_WEBHOOK else "未配置-仅生成报告不推送",
    )


_start_inspect_scheduler()


if __name__ == "__main__":
    if not (config.WECOM_TOKEN and config.WECOM_ENCODING_AES_KEY):
        logger.error("Token / EncodingAESKey 未配置，无法完成回调验证。请先配置 .env（参考 .env.example）")
        raise SystemExit(1)
    if config.WECOM_MODE == "app":
        if not config.WECOM_CORP_ID:
            logger.warning("WECOM_CORP_ID 未配置：回调 URL 验证将失败，请补充后重启")
        if not (config.WECOM_SECRET and config.WECOM_AGENT_ID):
            logger.warning("WECOM_SECRET / WECOM_AGENT_ID 未配置：可以启动并完成 URL 验证，但无法回复消息，请补充后重启")
    else:
        logger.info("运行模式: 智能机器人(aibot)，无需 CorpID/Secret/AgentId，回复通过 response_url 主动发送")
    logger.info("starting wecom-k8s-bot(mode=%s) on %s:%s%s", config.WECOM_MODE, config.SERVER_HOST, config.SERVER_PORT, config.WECOM_CALLBACK_PATH)
    # 生产环境建议改用 waitress: from waitress import serve; serve(app, host=..., port=...)
    app.run(host=config.SERVER_HOST, port=config.SERVER_PORT, threaded=True)
