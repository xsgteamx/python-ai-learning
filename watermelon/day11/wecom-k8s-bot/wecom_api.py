# -*- coding: utf-8 -*-
"""
企业微信服务端 API 封装：access_token 管理与消息发送。

- 单聊/应用消息：POST /cgi-bin/message/send
- 群聊消息：POST /cgi-bin/appchat/send（需应用可见范围为根部门，且群允许该应用发消息）
"""
import threading
import time

import requests

API_BASE = "https://qyapi.weixin.qq.com/cgi-bin"


class WeComApiError(Exception):
    pass


class WeComApi:
    def __init__(self, corp_id, secret, agent_id):
        self.corp_id = corp_id
        self.secret = secret
        self.agent_id = agent_id
        self._token = None
        self._token_expire_at = 0.0
        self._lock = threading.Lock()

    # ---------- access_token ----------
    def get_access_token(self):
        with self._lock:
            if self._token and time.time() < self._token_expire_at - 300:
                return self._token
            resp = requests.get(
                f"{API_BASE}/gettoken",
                params={"corpid": self.corp_id, "corpsecret": self.secret},
                timeout=10,
            )
            data = resp.json()
            if data.get("errcode", 0) != 0:
                raise WeComApiError(f"获取 access_token 失败: {data}")
            self._token = data["access_token"]
            self._token_expire_at = time.time() + int(data.get("expires_in", 7200))
            return self._token

    def _post(self, path, payload):
        token = self.get_access_token()
        resp = requests.post(
            f"{API_BASE}/{path}",
            params={"access_token": token},
            json=payload,
            timeout=20,
        )
        return resp.json()

    # ---------- 发送 ----------
    def send_to_user(self, user_id, content, agent_id=None):
        """发送到成员的应用会话（单聊）。"""
        payload = {
            "touser": user_id,
            "msgtype": "text",
            "agentid": int(agent_id or self.agent_id),
            "text": {"content": content},
            "safe": 0,
        }
        return self._post("message/send", payload)

    def send_to_chat(self, chat_id, content):
        """发送到群聊（appchat/send）。"""
        payload = {
            "chatid": chat_id,
            "msgtype": "text",
            "text": {"content": content},
        }
        return self._post("appchat/send", payload)

    def reply_via_response_url(self, response_url, content, msgtype="markdown"):
        """
        智能机器人主动回复：向回调消息携带的 response_url POST 明文 JSON。
        无需 access_token、无可信IP限制。每个 response_url 仅可调用一次，有效期 1 小时。
        """
        payload = {"msgtype": msgtype, msgtype: {"content": content}}
        resp = requests.post(response_url, json=payload, timeout=20)
        try:
            return resp.json()
        except ValueError:
            return {"errcode": -1, "errmsg": f"响应非 JSON: {resp.text[:200]}"}
