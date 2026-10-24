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
import llm_router

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


# 这些命令的查询结果会交给大模型生成 AI 分析（配了 LLM_API_KEY 时）
_AI_ENHANCE_CMDS = {"node_resources", "pod_detail", "crash_pods", "deploy_log", "k8s_query",
                     "cluster_inspect", "pod_last"}


def _unknown_reply(raw_text):
    """未定义问题的友好回复：闲聊 vs K8s 相关但没对上号，两种话术。"""
    k8s_words = ("k8s", "kubernetes", "集群", "pod", "日志", "deployment",
                 "节点", "命名空间", "容器", "service", "namespace", "详情",
                 "资源", "部署", "副本", "image", "镜像")
    hint = any(w in raw_text.lower() for w in k8s_words)
    if hint:
        return (
            "这个问题我还没完全理解，想帮你查但没对上号。\n\n"
            "你可以直接这样问我：\n"
            "  · 查看 coffee-mp 的日志\n"
            "  · 查看 coffee-mp 的错误日志 / 包含 ERROR 的日志\n"
            "  · 查看 deployment coffee-mp 的日志\n"
            "  · 查看 coffee-mp 的详情\n"
            "  · 看看哪些 pod 崩溃了\n\n"
            "发「帮助」可看全部支持的命令"
        )
    return (
        "哈哈，这个问题我还没学会呢，我就是个 K8s 运维小助手，\n"
        "主业是看集群、查日志、排故障。\n\n"
        "你可以试试这样问我：\n"
        "  · 查看 coffee-mp 的日志\n"
        "  · 看看哪些 pod 崩溃了\n"
        "  · 查看节点资源占用\n\n"
        "发「帮助」可看我的全部技能"
    )


def _cost_header(cost_sec, force=False):
    """查询耗时提示头部；耗时小于阈值且未发过思考提示时不显示。"""
    import config as _cfg
    if not force and cost_sec < _cfg.QUERY_COST_SHOW_SEC:
        return ""
    return f"⏱ 查询耗时 {cost_sec:.1f} 秒\n"


def _ai_enhance(cmd, params, result):
    """对适合分析的查询结果附加 AI 分析；pod_log 仅错误筛选/关键词过滤时增强。"""
    if cmd in _AI_ENHANCE_CMDS:
        return llm_router.enhance_reply(cmd, result)
    if cmd == "pod_log" and (params.get("error_only") or params.get("filter")):
        return llm_router.enhance_reply("pod_log", result)
    return result


def build_reply(cmd, params):
    """根据意图执行查询，返回回复文本。任何异常都转成可读错误。"""
    try:
        if cmd == "log_help":
            return (
                "查看 Pod 日志用法：\n"
                "  · 查看 <pod名> 的日志           默认最近60行\n"
                "  · 日志 <命名空间>/<pod名> 后300行  指定行数\n"
                "  · 查看 <pod名> 的错误日志       只看错误行\n"
                "  · 查看 <pod名> 的日志 包含 <关键词>  按关键词筛（大小写不限，支持 INFO/ERROR 等）\n\n"
                "日志超长会自动保存并提供下载链接"
            )
        if cmd == "detail_help":
            return (
                "查看 Pod 详情用法：\n"
                "  · 查看 <pod名> 的详情\n"
                "  · describe po <pod名> -n <命名空间>\n\n"
                "会显示：状态、重启次数、退出原因、上次退出时间、事件等"
            )
        if cmd == "removed_cluster_resources":
            return (
                "「查看集群资源占用」命令已移除。\n"
                "如需查看资源使用情况，请使用：\n"
                "  查看节点资源占用（等价 kubectl top node）\n"
                "发「帮助」可查看全部指令。"
            )
        if cmd == "cluster_inspect":
            return _ai_enhance(cmd, params, k8s_api.cluster_inspect_report())
        if cmd == "readonly_denied":
            return (
                "⚠️ 无法执行非查询操作。\n"
                "我是 K8s 只读查询助手，只支持查看类查询（节点资源、Pod 日志/详情、"
                "命名空间、Deployment 日志、崩溃排查等），不支持重启/删除/修改/扩容等变更操作。\n"
                "发「帮助」可查看完整能力清单。"
            )
        if cmd == "node_resources":
            return _ai_enhance(cmd, params, k8s_api.node_resources_report())
        if cmd == "pod_log":
            return _ai_enhance(cmd, params, k8s_api.pod_log_report(
                params.get("pod"), params.get("lines"),
                params.get("error_only"), params.get("filter")))
        if cmd == "pod_detail":
            return _ai_enhance(cmd, params, k8s_api.pod_detail_report(
                params.get("pod"), params.get("namespace")))
        if cmd == "pod_last":
            return _ai_enhance(cmd, params, k8s_api.pod_last_state_report(
                params.get("pod"), params.get("namespace")))
        if cmd == "deploy_log":
            return _ai_enhance(cmd, params, k8s_api.deploy_log_report(
                params.get("deployment"), params.get("namespace"), params.get("lines"),
                params.get("all_pods")))
        if cmd == "k8s_query":
            return _ai_enhance(cmd, params, k8s_api.k8s_query_report(
                params.get("resource"), params.get("action"),
                params.get("name"), params.get("namespace"), params.get("label")))
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
        if cmd == "crash_pods":
            return _ai_enhance(cmd, params, k8s_api.crash_pods_report())
        if cmd == "unknown":
            return _unknown_reply(params.get("text", ""))
        if cmd == "crash_help":
            return (
                "排查 Pod 崩溃/异常，需要知道具体是哪个 Pod，例如：\n"
                "  coffee-xxx 崩了              → 自动查看它的错误日志\n"
                "  查看 coffee-xxx 的错误日志    → 筛最近 200 行错误\n"
                "  查看 coffee-xxx 的详情        → 看退出原因 / 上次退出\n"
                "还没有 Pod 名？可先发：\n"
                "  查看命名空间\n"
                "  查看 xxx 命名空间 的 pod"
            )
        return intent.HELP_TEXT
    except k8s_api.K8sQueryError as exc:
        return f"查询失败：{exc}"
    except Exception as exc:  # 兜底
        logger.exception("query failed")
        return f"查询出错：{type(exc).__name__}: {exc}"
