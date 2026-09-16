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
import threading
import time
import xml.etree.ElementTree as ET

from flask import Flask, Response, abort, request, send_from_directory

import config
import intent
import k8s_api
from replies import _dedupe, _truncate, build_reply
from wecom_api import WeComApi, WeComApiError
from wx_crypt import WXBizMsgCrypt, WeComCryptoError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("wecom-k8s-bot")

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

    reply = _truncate(_build_reply(cmd, params), config.MAX_REPLY_BYTES)
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
