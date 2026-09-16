# -*- coding: utf-8 -*-
"""
智能机器人·长连接（WebSocket）客户端 —— 内网部署专用

适用场景：服务部署在内网，无公网回调地址。由本服务主动向企业微信建立
WebSocket 长连接（wss://openws.work.weixin.qq.com），无需消息加解密。

协议（详见官方文档 path/101463）：
  1. 连接后发送 aibot_subscribe（携带 BotID + Secret）完成订阅
  2. 收到 aibot_msg_callback（用户消息，明文 JSON）
  3. 用 aibot_respond_msg 回复（透传回调的 req_id，支持 markdown）
  4. 每 30 秒发送应用层 ping 保持心跳；断线自动指数退避重连

配置（.env）：
  WECOM_MODE=aibot_ws
  WECOM_WS_BOT_ID=<智能机器人 API设置·长连接 页面里的 BotID>
  WECOM_WS_SECRET=<长连接专用 Secret>
  # WECOM_WS_URL / WECOM_WS_HEARTBEAT 可选

用法：
  python aibot_ws.py
  或由 docker-compose 的 wecom-k8s-bot-ws 服务启动
"""
import json
import logging
import random
import string
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import websocket  # websocket-client

import config
import intent
import k8s_api
from replies import _dedupe, _truncate, build_reply  # 复用回复构建/去重

logger = logging.getLogger("aibot_ws")


def _gen_req_id():
    return uuid.uuid4().hex[:24]


class AibotWSClient:
    """智能机器人长连接客户端（单连接 + 自动重连 + 心跳）。"""

    def __init__(self, bot_id, secret, url, heartbeat=30, max_workers=4):
        self.bot_id = bot_id
        self.secret = secret
        self.url = url
        self.heartbeat = heartbeat
        self.ws = None
        self._send_lock = threading.Lock()
        self._stop = threading.Event()
        self._heartbeat_stop = threading.Event()
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="aibot_ws_reply")

    # ---------- 发送 ----------
    def _send(self, payload):
        with self._send_lock:
            if self.ws is not None:
                self.ws.send(json.dumps(payload, ensure_ascii=False))
                return True
        return False

    def _send_subscribe(self):
        self._send({
            "cmd": "aibot_subscribe",
            "headers": {"req_id": _gen_req_id()},
            "body": {"bot_id": self.bot_id, "secret": self.secret},
        })
        logger.info("已发送订阅请求（bot_id=%s）", self.bot_id)

    # ---------- 接收分发 ----------
    def _handle_frame(self, frame):
        try:
            msg = json.loads(frame)
        except ValueError:
            logger.warning("无法解析的消息帧: %.200s", frame)
            return
        cmd = msg.get("cmd", "")
        if cmd == "aibot_msg_callback":
            self._executor.submit(self._on_msg_callback, msg)
        elif cmd == "aibot_event_callback":
            self._on_event_callback(msg)
        elif cmd in ("ping", "pong"):
            pass  # 应用层心跳响应，无需处理
        else:
            errcode = msg.get("errcode", 0)
            if errcode != 0:
                logger.warning("企微返回错误: %s", msg)

    def _on_msg_callback(self, msg):
        req_id = (msg.get("headers") or {}).get("req_id", "")
        body = msg.get("body", {})
        if body.get("msgtype") != "text":
            logger.info("[ws] 忽略非文本消息 msgtype=%s", body.get("msgtype"))
            return
        msgid = body.get("msgid")
        if not _dedupe(msgid):
            return
        content = (body.get("text") or {}).get("content", "")
        cmd, params = intent.parse_intent(content)
        logger.info("[ws] cmd=%s params=%s chattype=%s chatid=%s", cmd, params, body.get("chattype"), body.get("chatid"))
        reply = _truncate(build_reply(cmd, params), config.MAX_REPLY_BYTES)
        payload = {
            "cmd": "aibot_respond_msg",
            "headers": {"req_id": req_id},
            "body": {"msgtype": config.AIBOT_REPLY_TYPE, config.AIBOT_REPLY_TYPE: {"content": reply}},
        }
        if self._send(payload):
            logger.info("[ws] 已回复 req_id=%s", req_id)
        else:
            logger.error("[ws] 回复发送失败（连接已断开）：req_id=%s", req_id)

    def _on_event_callback(self, msg):
        body = msg.get("body", {})
        event = body.get("event", {})
        logger.info("[ws] 事件回调 eventtype=%s", event.get("eventtype"))

    # ---------- 心跳 ----------
    def _heartbeat_loop(self):
        self._heartbeat_stop.clear()
        while not self._heartbeat_stop.is_set() and not self._stop.is_set():
            self._heartbeat_stop.wait(self.heartbeat)
            if self._heartbeat_stop.is_set() or self._stop.is_set():
                break
            self._send({"cmd": "ping", "headers": {"req_id": _gen_req_id()}})
            logger.debug("[ws] 心跳 ping")

    # ---------- 连接主循环 ----------
    def run_forever(self):
        """阻塞运行：连接 -> 订阅 -> 收消息，断线指数退避自动重连。"""
        backoff = 1
        while not self._stop.is_set():
            try:
                self._connect_once()
                backoff = 1
            except Exception as exc:
                logger.error("[ws] 长连接异常: %s（%.0f 秒后重连）", exc, backoff)
            if self._stop.is_set():
                break
            self._stop.wait(min(backoff, 60))
            backoff = min(backoff * 2, 60)

    def _connect_once(self):
        self.ws = websocket.WebSocketApp(
            self.url,
            on_open=self._on_open,
            on_message=lambda _ws, frame: self._handle_frame(frame),
            on_error=lambda _ws, err: logger.error("[ws] 连接错误: %s", err),
            on_close=lambda _ws, *args: logger.info("[ws] 连接已关闭"),
        )
        logger.info("[ws] 连接 %s ...", self.url)
        self.ws.run_forever()

    def _on_open(self, _ws):
        logger.info("[ws] 连接建立成功，发送订阅")
        self._send_subscribe()
        threading.Thread(target=self._heartbeat_loop, daemon=True).start()

    def stop(self):
        self._stop.set()
        self._heartbeat_stop.set()
        if self.ws is not None:
            try:
                self.ws.close()
            except Exception:
                pass


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    if not (config.WECOM_WS_BOT_ID and config.WECOM_WS_SECRET):
        logger.error("缺少长连接凭证：请设置 WECOM_WS_BOT_ID 与 WECOM_WS_SECRET（.env，参考 .env.example）")
        raise SystemExit(1)
    client = AibotWSClient(
        bot_id=config.WECOM_WS_BOT_ID,
        secret=config.WECOM_WS_SECRET,
        url=config.WECOM_WS_URL,
        heartbeat=config.WECOM_WS_HEARTBEAT,
    )
    logger.info("智能机器人长连接客户端启动（url=%s，心跳=%ss）", config.WECOM_WS_URL, config.WECOM_WS_HEARTBEAT)
    try:
        client.run_forever()
    except KeyboardInterrupt:
        client.stop()
        logger.info("已停止")


if __name__ == "__main__":
    main()
