# -*- coding: utf-8 -*-
"""
企业微信消息加解密（与官方 WXBizMsgCrypt 兼容，纯 Python 实现）。

协议要点（见官方《加解密方案说明》）：
- EncodingAESKey 为 43 位随机字符串，AESKey = Base64_Decode(EncodingAESKey + "=")，32 字节。
- AES-256-CBC，IV 取 AESKey 前 16 字节，PKCS#7 填充到 32 字节倍数。
- 明文 = random(16B) + msg_len(4B, 网络字节序) + msg + receiveid
- 签名 msg_signature = sha1(sort(token, timestamp, nonce, msg_encrypt))，字典序拼接后小写 hex。
"""
import base64
import hashlib
import json
import os
import re
import struct

from Crypto.Cipher import AES


class WeComCryptoError(Exception):
    """加解密或校验失败时抛出。"""


class WXBizMsgCrypt:
    def __init__(self, token, encoding_aes_key, receive_id):
        self.token = token or ""
        # 应用回调场景 receiveid = 企业 CorpID
        self.receive_id = receive_id or ""
        if not encoding_aes_key or len(encoding_aes_key) != 43:
            raise WeComCryptoError("EncodingAESKey 长度必须为 43 位")
        self.aes_key = base64.b64decode(encoding_aes_key + "=")
        if len(self.aes_key) != 32:
            raise WeComCryptoError("AESKey 非法（Base64 解码后应为 32 字节）")

    # ---------- 签名 ----------
    def _signature(self, timestamp, nonce, msg_encrypt):
        sort_list = sorted([self.token, str(timestamp), str(nonce), msg_encrypt])
        raw = "".join(sort_list).encode("utf-8")
        return hashlib.sha1(raw).hexdigest()

    # ---------- 验证 URL（保存回调配置时的 GET 请求） ----------
    def verify_url(self, msg_signature, timestamp, nonce, echostr):
        if not echostr:
            return None
        if self._signature(timestamp, nonce, echostr) != msg_signature:
            return None
        return self._decrypt(echostr)

    # ---------- 解密回调 POST 数据 ----------
    @staticmethod
    def _extract_encrypt(post_data):
        """
        从回调请求体中提取密文字段。
        自建应用：XML <Encrypt><![CDATA[...]]></Encrypt>
        智能机器人：JSON {"encrypt": "..."}
        """
        post_data = (post_data or "").strip()
        if post_data.startswith("{"):
            try:
                return json.loads(post_data).get("encrypt", "") or ""
            except ValueError:
                return ""
        m = re.search(r"<Encrypt><!\[CDATA\[(.*?)\]\]></Encrypt>", post_data, re.S)
        return m.group(1) if m else ""

    def decrypt_msg(self, msg_signature, timestamp, nonce, post_data):
        """
        输入为回调请求体（XML 或 JSON，两种格式均可），内部自动提取密文。
        签名校验失败返回 None；解密失败抛 WeComCryptoError。
        """
        encrypt = self._extract_encrypt(post_data)
        if not encrypt:
            raise WeComCryptoError("请求体中未找到 Encrypt 密文")
        if self._signature(timestamp, nonce, encrypt) != msg_signature:
            return None
        return self._decrypt(encrypt)

    def _decrypt(self, encrypted):
        iv = self.aes_key[:16]
        cipher = AES.new(self.aes_key, AES.MODE_CBC, iv)
        raw = cipher.decrypt(base64.b64decode(encrypted))
        pad = raw[-1]
        if pad < 1 or pad > 32:
            raise WeComCryptoError("解密后填充长度非法")
        raw = raw[:-pad]
        if len(raw) < 20:
            raise WeComCryptoError("解密后数据过短")
        msg_len = struct.unpack(">I", raw[16:20])[0]
        msg = raw[20:20 + msg_len].decode("utf-8")
        receive_id = raw[20 + msg_len:].decode("utf-8")
        if receive_id != self.receive_id:
            raise WeComCryptoError("ReceiveId 校验失败")
        return msg

    # ---------- 加密（被动回复用，保留备用） ----------
    def encrypt_msg(self, reply_msg, timestamp, nonce):
        random16 = os.urandom(16)
        msg_bytes = reply_msg.encode("utf-8")
        raw = random16 + struct.pack(">I", len(msg_bytes)) + msg_bytes + self.receive_id.encode("utf-8")
        pad = 32 - (len(raw) % 32)
        raw += bytes([pad]) * pad
        iv = self.aes_key[:16]
        cipher = AES.new(self.aes_key, AES.MODE_CBC, iv)
        return base64.b64encode(cipher.encrypt(raw)).decode("utf-8")

    def encrypt_response(self, reply_msg, timestamp, nonce):
        """构造被动回复所需的加密 XML 响应包（自建应用）。"""
        encrypt = self.encrypt_msg(reply_msg, timestamp, nonce)
        signature = self._signature(timestamp, nonce, encrypt)
        return (
            "<xml>"
            f"<Encrypt><![CDATA[{encrypt}]]></Encrypt>"
            f"<MsgSignature><![CDATA[{signature}]]></MsgSignature>"
            f"<TimeStamp>{timestamp}</TimeStamp>"
            f"<Nonce><![CDATA[{nonce}]]></Nonce>"
            "</xml>"
        )

    def encrypt_payload(self, reply_msg, timestamp, nonce, fmt="json"):
        """构造被动回复的加密数据包。fmt=json 用于智能机器人，xml 用于自建应用。"""
        encrypt = self.encrypt_msg(reply_msg, timestamp, nonce)
        signature = self._signature(timestamp, nonce, encrypt)
        if fmt == "json":
            return json.dumps(
                {
                    "encrypt": encrypt,
                    "msgsignature": signature,
                    "timestamp": int(timestamp),
                    "nonce": nonce,
                },
                ensure_ascii=False,
            )
        return self.encrypt_response(reply_msg, timestamp, nonce)
