# -*- coding: utf-8 -*-
"""
回复构建公共模块：意图执行、文本截断、消息去重。
供 app.py（URL回调/自建应用）与 aibot_ws.py（长连接）共同复用。
"""
import logging
import threading
import time

import config
import intent
import k8s_api

logger = logging.getLogger("wecom-k8s-bot")

# MsgId 去重缓存（企业微信回调失败会重试推送，避免重复处理/重复回复）
_seen_msgs = {}
_seen_msgs_lock = threading.Lock()
_MSG_TTL = 600  # 10 分钟内去重


def _dedupe(msg_id):
    if not msg_id:
        return True
    now = time.time()
    with _seen_msgs_lock:
        if len(_seen_msgs) > 5000:
            for k in [k for k, v in _seen_msgs.items() if now - v > _MSG_TTL]:
                _seen_msgs.pop(k, None)
        if msg_id in _seen_msgs:
            return False
        _seen_msgs[msg_id] = now
        return True


def _truncate(text, max_bytes):
    """按字节截断，保证不超过企业微信消息长度上限。"""
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    cut = encoded[: max_bytes - len("（内容过长已截断，可缩小查询范围）".encode("utf-8"))]
    return cut.decode("utf-8", errors="ignore") + "（内容过长已截断，可缩小查询范围）"


def build_reply(cmd, params):
    """根据意图执行查询，返回回复文本。任何异常都转成可读错误。"""
    try:
        if cmd == "removed_cluster_resources":
            return (
                "「查看集群资源占用」命令已移除。\n"
                "如需查看资源使用情况，请使用：\n"
                "  查看节点资源占用（等价 kubectl top node）\n"
                "发「帮助」可查看全部指令。"
            )
        if cmd == "node_resources":
            return k8s_api.node_resources_report()
        if cmd == "pod_log":
            return k8s_api.pod_log_report(params.get("pod"), params.get("lines"))
        if cmd == "pod_detail":
            return k8s_api.pod_detail_report(params.get("pod"), params.get("namespace"))
        if cmd == "pod_list":
            ns = params.get("namespace")
            if not ns:
                return (
                    "请指定命名空间，例如：\n"
                    "  查看 kube-system 的 pod\n"
                    "  看看 default 命名空间下的 pod\n"
                    "可先发「查看命名空间」看看有哪些命名空间"
                )
            return k8s_api.list_pods(params.get("keyword"), namespace=ns)
        if cmd == "list_namespaces":
            return k8s_api.list_namespaces()
        return intent.HELP_TEXT
    except k8s_api.K8sQueryError as exc:
        return f"查询失败：{exc}"
    except Exception as exc:  # 兜底
        logger.exception("query failed")
        return f"查询出错：{type(exc).__name__}: {exc}"
