# -*- coding: utf-8 -*-
"""
意图解析：把用户在企微里输入的自然语言转成内部命令。

支持命令：
  removed_cluster_resources  旧「集群资源占用」命令的引导提示
  node_resources      查看每个节点的资源占用（kubectl top node）
  pod_log             查看某个 Pod 的日志
  pod_list            列出 Pod
  help                帮助
"""
import re

from config import K8S_LOG_DEFAULT_LINES, K8S_LOG_MAX_LINES

HELP_TEXT = (
    "你好，我是 K8s 查询助手。支持以下指令：\n"
    "1. 查看每个工作节点的资源占用情况\n"
    "   例：查看节点资源占用 / top node\n"
    "2. 查看某个 Pod 的日志\n"
    "   例：查看 nginx-abc-123 的日志\n"
    "   例：日志 default/nginx-abc-123 后300行\n"
    "默认输出60行，超过后，转换成文本\n"
    "3. 列出某个命名空间下的 Pod\n"
    "   例：查看 kube-system 的 pod\n"
    "   例：看看 default 命名空间下的 pod\n"
    "4. 查看 Pod 详情\n"
    "   例：describe po coffee-mp-release-v2-f8968694c-zpwdf -n coffee-system\n"
    "   例：查看 coffee-mp-release-v2-f8968694c-zpwdf 的详情\n"
    "5. 查看命名空间\n   例：看看有哪些命名空间\n"
    "6. 查看pod日志\n" \
    "展示用法\n"
    "7. 查看pod详情\n"
    "展示用法\n"
    "8. 帮助\n"
        "   例：帮助 / help\n"
)

_MENTION_RE = re.compile(r"^\s*@[^\s@，,。]{1,50}[\s，,。]*")

# 命名空间提取：值在前（"kube-system 命名空间"）或 值在后（"namespace kube-system"）
_NS_RE = re.compile(r"([a-z0-9][a-z0-9._-]*)\s*(?:命名空间|namespace)")
_NS_REV_RE = re.compile(r"(?:命名空间|namespace)\s*[:：]?\s*([a-z0-9][a-z0-9._-]*)")
# “xxx 下的 pod / xxx 里的 pod / xxx 的 pod”
_NS_POD_RE = re.compile(r"([a-z0-9][a-z0-9._-]*)\s*(?:下|里|中|内|的)\s*的?\s*pod")
# kubectl 风格: -n coffee-system / -ncoffee-system（-n 前必须是空白/行首，避免误吃 Pod 名里的 “-n”）
_NS_DASH_RE = re.compile(r"(?:^|\s)-n\s*[:：]?\s*([a-z0-9][a-z0-9._-]*)")


def strip_mention(text):
    """去掉群聊@场景下 Content 开头附带的 “@机器人名 ” 前缀。"""
    return _MENTION_RE.sub("", text.strip(), count=1)


def _extract_namespace(text):
    """从文本中提取命名空间名；未指定时返回 None。"""
    low = (text or "").strip().lower()
    if not low:
        return None
    for pat in (_NS_RE, _NS_REV_RE, _NS_POD_RE, _NS_DASH_RE):
        m = pat.search(low)
        if m:
            ns = m.group(1)
            if ns not in ("某个", "那个", "这个", "指定", "哪些"):
                return ns
    return None


def _extract_pod_and_lines(text):
    """从文本里提取 (pod 名, 日志行数)。pod 名形如 nginx-abc-123 或 ns/nginx-abc-123。"""
    t = text.strip()
    lines = K8S_LOG_DEFAULT_LINES

    # “最近 20 行 / 后300行 / 只取50条 / 20 行” 均可识别（前缀可选）
    m = re.search(r"(?:(?:后|最近|最后|取|只)\s*)?(\d{1,5})\s*(?:行|条)", t)
    if m:
        lines = min(max(int(m.group(1)), 1), K8S_LOG_MAX_LINES)

    pod_pattern = r"[A-Za-z0-9][A-Za-z0-9._\-]*(?:/[A-Za-z0-9][A-Za-z0-9._\-]*)?"
    # “xxx 的日志” / “xxx日志” / “xxx 最近 50 行的日志”
    m = re.search(r"(" + pod_pattern + r")[^\n]{0,25}?日志", t)
    if m:
        pod = m.group(1)
        if pod.lower() not in ("pod", "容器", "日志") and not pod.isdigit():
            return pod, lines
    # “日志 xxx” / “日志 ns/xxx 后 N 行”
    m = re.search(r"日志[\s,，:：]*(?:的|查看)?\s*(" + pod_pattern + r")", t)
    if m:
        pod = m.group(1)
        if pod.lower() not in ("pod", "容器", "日志") and not pod.isdigit():
            return pod, lines
    return None, lines


def _extract_pod_detail(text):
    """从文本提取 (pod 名, 命名空间)，用于查看 Pod 详情。"""
    t = text.strip()
    low = t.lower()
    ns = _extract_namespace(t)
    pod_pattern = r"[A-Za-z0-9][A-Za-z0-9._\-]*(?:/[A-Za-z0-9][A-Za-z0-9._\-]*)?"
    # 1) kubectl 风格: describe po/pod xxx [-n ns]
    m = re.search(r"describe\s+(?:po|pod|pods)\s*[:：]?\s*(" + pod_pattern + r")", low)
    # 2) “xxx 的详情 / xxx 的 pod 详情”
    if not m:
        m = re.search(r"(" + pod_pattern + r")\s*的?\s*(?:pod\s*)?详情", low)
    # 3) “pod详情 xxx / 详情 xxx”
    if not m:
        m = re.search(r"(?:pod|容器)?\s*详情\s*[:：]?\s*(" + pod_pattern + r")", low)
    if not m:
        return None, ns
    pod = m.group(1)
    if pod.lower() in ("pod", "容器", "日志", "describe") or pod.isdigit():
        return None, ns
    return pod, ns


def parse_intent(text):
    """返回 (command, params)。"""
    t = strip_mention(text)
    low = t.lower()

    if not t:
        return "help", {}

    if any(k in low for k in ("帮助", "help", "指令", "命令", "功能", "用法")):
        return "help", {}

    if "日志" in low:
        pod, lines = _extract_pod_and_lines(t)
        return "pod_log", {"pod": pod, "lines": lines}

    # Pod 详情（等价 kubectl describe po；优先于 pod_list/命名空间，避免被抢）
    if "describe" in low:
        pod, ns = _extract_pod_detail(t)
        return "pod_detail", {"pod": pod, "namespace": ns}
    if "详情" in low:
        pod, ns = _extract_pod_detail(t)
        # 能提取到 Pod 名，或明确提到 pod（如“查看 pod 详情”），才走详情；
        # 否则继续往下（避免“查看节点资源详情/命名空间详情”被误伤）
        if pod or "pod" in low:
            return "pod_detail", {"pod": pod, "namespace": ns}

    # Pod 列表（优先于命名空间分支，避免“xx命名空间的pod”被命名空间命令抢走）
    if "pod" in low:
        return "pod_list", {"keyword": None, "namespace": _extract_namespace(t)}

    # 节点资源占用（等价 kubectl top node）
    if any(k in low for k in (
        "节点资源", "节点占用", "节点用量", "节点使用", "节点负载",
        "节点cpu", "节点内存", "节点状态", "每个节点", "各节点",
        "node资源", "node占用", "node用量", "node使用",
        "nodetop", "topnode", "node top", "top node",
    )):
        return "node_resources", {}

    # 已移除命令的引导提示（原「查看集群资源占用情况」）
    if any(k in low for k in ("集群资源", "集群占用", "集群用量", "集群使用", "集群容量",
                              "k8s资源", "k8s占用", "k8s用量", "k8s容量")):
        return "removed_cluster_resources", {}

    if any(k in low for k in ("命名空间", "namespace", "ns列表")):
        return "list_namespaces", {}

    return "help", {}
