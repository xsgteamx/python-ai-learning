# -*- coding: utf-8 -*-
"""
Kubernetes 查询模块：集群资源占用、Pod 日志、Pod 列表。

依赖官方 python client（pip install kubernetes），配置来源：
- 优先使用当前环境 kubeconfig（KUBECONFIG 或 ~/.kube/config）
- 若运行在集群内（Pod 中），自动使用 in-cluster 配置
"""
import ast
import datetime
import os
import re

from kubernetes import client, config
from kubernetes.client.rest import ApiException

from config import (
    KUBECONFIG, K8S_LOG_DEFAULT_LINES, K8S_LOG_MAX_LINES, MAX_REPLY_BYTES,
    LOG_DOWNLOAD_BASE_URL,
)

# 超长日志落盘目录：项目根目录下 logs/（容器内为 /app/logs，docker-compose 已挂载宿主机 ./logs）
LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")


class K8sQueryError(Exception):
    """封装 k8s 查询过程中的可读错误。"""


def _load_v1():
    try:
        config.load_incluster_config()
    except Exception:
        try:
            config.load_kube_config(config_file=KUBECONFIG)
        except Exception as exc:  # ConfigException 等
            raise K8sQueryError(f"无法加载 Kubernetes 配置（kubeconfig）: {exc}")
    return client.CoreV1Api()


def _custom_api():
    return client.CustomObjectsApi()


# ---------------- 资源量换算 ----------------
# CPU 常见单位：m（毫核）、n（纳核，metrics-server 节点用量常用）、u（微核）
_CPU_SUFFIX = [("n", 1e-9), ("u", 1e-6), ("m", 1e-3)]


def cpu_to_cores(q):
    """'4' / '2500m' / '23886627n' / 4.5 -> 核数 float"""
    q = str(q).strip()
    for suffix, factor in _CPU_SUFFIX:
        if q.endswith(suffix):
            try:
                return float(q[: -len(suffix)]) * factor
            except ValueError:
                return 0.0
    try:
        return float(q)
    except ValueError:
        return 0.0


_MEM_UNITS = [
    ("Ti", 2 ** 40), ("Gi", 2 ** 30), ("Mi", 2 ** 20), ("Ki", 2 ** 10),
    ("T", 10 ** 12), ("G", 10 ** 9), ("M", 10 ** 6), ("K", 10 ** 3),
    ("m", 10 ** -3),
]


def mem_to_gib(q):
    """'64Gi' / '64000Mi' / '68719476736'(字节) -> GiB float"""
    q = str(q).strip()
    for suffix, factor in _MEM_UNITS:
        if q.endswith(suffix):
            try:
                return float(q[: -len(suffix)]) * factor / (2 ** 30)
            except ValueError:
                return 0.0
    try:
        return float(q) / (2 ** 30)
    except ValueError:
        return 0.0


def _pod_requests(pod):
    """返回 Pod 内所有容器的 requests 总量 (cpu_cores, mem_gib)。"""
    cpu = 0.0
    mem = 0.0
    for c in pod.spec.containers or []:
        req = c.resources.requests if c.resources and c.resources.requests else {}
        cpu += cpu_to_cores(req.get("cpu", "0"))
        mem += mem_to_gib(req.get("memory", "0"))
    return cpu, mem


def _pct(part, total):
    return 0.0 if not total else part / total * 100.0


# ---------------- 节点资源占用（等价 kubectl top node） ----------------
def node_resources_report():
    """每个节点的 CPU/内存实际用量（metrics-server），等价 kubectl top node。"""
    v1 = _load_v1()
    try:
        nodes = v1.list_node().items
    except ApiException as exc:
        raise K8sQueryError(f"访问 Kubernetes API 失败: {exc}")

    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [f"【节点资源占用】{now}", "数据来源: metrics-server（等价 kubectl top node）"]

    try:
        metrics = _custom_api().list_cluster_custom_object(
            group="metrics.k8s.io", version="v1beta1", plural="nodes"
        )
    except ApiException:
        lines.append("未检测到 metrics-server，无法给出各节点实际用量。")
        lines.append("请确认已部署 metrics-server（kubectl top node 依赖它）。")
        return "\n".join(lines)

    node_alloc = {}
    for node in nodes:
        alloc = node.status.allocatable or {}
        node_alloc[node.metadata.name] = (
            cpu_to_cores(alloc.get("cpu", "0")),
            mem_to_gib(alloc.get("memory", "0")),
        )
    node_usage = {}
    for item in metrics.get("items", []):
        name = item["metadata"]["name"]
        usage = item.get("usage", {})
        node_usage[name] = (
            cpu_to_cores(usage.get("cpu", "0")),
            mem_to_gib(usage.get("memory", "0")),
        )

    if not node_usage:
        lines.append("未获取到节点 metrics 数据（metrics-server 可能刚部署或异常）。")
        return "\n".join(lines)

    lines.append(f"节点数: {len(nodes)}")
    cluster_cpu_u = cluster_mem_u = 0.0
    cluster_cpu_a = cluster_mem_a = 0.0
    for name, (cpu_a, mem_a) in sorted(node_alloc.items()):
        if name not in node_usage:
            lines.append(f"  {name}: （暂无 metrics 数据）")
            continue
        cpu_u, mem_u = node_usage[name]
        cluster_cpu_u += cpu_u
        cluster_mem_u += mem_u
        cluster_cpu_a += cpu_a
        cluster_mem_a += mem_a
        lines.append(
            f"  {name}: CPU {cpu_u:.1f}/{cpu_a:.1f} 核 ({_pct(cpu_u, cpu_a):.0f}%) | "
            f"内存 {mem_u:.1f}/{mem_a:.1f} Gi ({_pct(mem_u, mem_a):.0f}%)"
        )
    lines.append(
        f"  集群合计: CPU {cluster_cpu_u:.1f}/{cluster_cpu_a:.1f} 核 "
        f"({_pct(cluster_cpu_u, cluster_cpu_a):.0f}%) | "
        f"内存 {cluster_mem_u:.1f}/{cluster_mem_a:.1f} Gi "
        f"({_pct(cluster_mem_u, cluster_mem_a):.0f}%)"
    )
    return "\n".join(lines)




# ---------------- 查看命名空间列表 ----------------

def list_namespaces():
    v1 = _load_v1()
    try:
        nss = v1.list_namespace().items
    except ApiException as exc:
        raise K8sQueryError(f"访问 Kubernetes API 失败: {exc}")
    names = sorted(ns.metadata.name for ns in nss)
    return f"【命名空间列表】共 {len(names)} 个\n" + "\n".join(f"  {n}" for n in names)













# ---------------- Pod 列表 ----------------
def list_pods(keyword=None, namespace=None, limit=None):
    v1 = _load_v1()
    try:
        if namespace:
            pods = v1.list_namespaced_pod(namespace=namespace).items
        else:
            pods = v1.list_pod_for_all_namespaces().items
    except ApiException as exc:
        raise K8sQueryError(f"访问 Kubernetes API 失败: {exc}")

    kw = (keyword or "").strip().lower()
    items = []
    for pod in pods:
        name = pod.metadata.name
        if kw and kw not in name.lower():
            continue
        items.append((pod.metadata.namespace, name, pod.status.phase or "?"))
    items.sort(key=lambda x: (x[0], x[1]))

    if namespace:
        lines = [f"【Pod 列表】命名空间 {namespace}，共 {len(items)} 个（格式：Pod名 [状态]）"
                 + (f"（匹配: {keyword}）" if kw else "")]
    else:
        lines = [f"【Pod 列表】共 {len(items)} 个（格式：命名空间/Pod名 [状态]）"
                 + (f"（匹配: {keyword}）" if kw else "")]
    if not items:
        lines.append("没有找到匹配的 Pod")
    shown = items if not limit else items[:limit]
    for ns, name, phase in shown:
        lines.append(f"  {ns}/{name}  [{phase}]")
    if limit and len(items) > limit:
        lines.append(f"  ... 共 {len(items)} 个，仅显示前 {limit} 个（完整列表受企微单条消息 20480 字节限制）")
    return "\n".join(lines)


# ---------------- Pod 详情（等价 kubectl describe po） ----------------
_DETAIL_HELP = (
    "查看 Pod 详情需要指定 Pod，支持三种写法：\n"
    "1. kubectl 风格：\n"
    "   describe po coffee-mp-release-v2-f8968694c-zpwdf -n coffee-system\n"
    "2. 命名空间 + Pod 名：\n"
    "   查看 coffee-system 的 pod coffee-mp-release-v2-f8968694c-zpwdf 的详情\n"
    "3. 只看名称（自动在所有命名空间查找）：\n"
    "   查看 coffee-mp-release-v2-f8968694c-zpwdf 的详情\n"
    "可先发「查看命名空间」或「查看 xxx 的 pod」确定名称"
)


def _container_state(cs):
    """container_status 的状态描述（含退出原因/退出码/等待原因）。"""
    st = cs.state
    if not st:
        return "?"
    if st.running:
        return f"Running（启动于 {st.running.started_at}）"
    if st.waiting:
        # CrashLoopBackOff / ImagePullBackOff / CreateContainerConfigError 等
        s = f"Waiting（{st.waiting.reason or '?'}"
        if st.waiting.message:
            msg = st.waiting.message.replace("\n", " ")
            s += f": {msg[:80]}{'…' if len(msg) > 80 else ''}"
        return s + "）"
    if st.terminated:
        # OOMKilled / Error / Completed 等：reason + 退出码 + 信号 + 结束时间
        s = f"Terminated（{st.terminated.reason or 'exit'} {st.terminated.exit_code}"
        if st.terminated.signal:
            s += f" 信号 {st.terminated.signal}"
        if st.terminated.finished_at:
            s += f" 于 {st.terminated.finished_at}"
        return s + "）"
    return "?"


def _pod_events(namespace, name, limit=5):
    """取该 Pod 最近的事件（对应 kubectl describe 底部 Events 区）。"""
    v1 = _load_v1()
    try:
        events = v1.list_namespaced_event(
            namespace=namespace,
            field_selector=f"involvedObject.name={name}",
            limit=limit,
        ).items
    except ApiException:
        return []
    events.sort(
        key=lambda e: e.last_timestamp or e.event_time
        or e.metadata.creation_timestamp or datetime.datetime.min,
        reverse=True,
    )
    return events[:limit]


def pod_detail_report(pod, namespace=None):
    """查看 Pod 详情，等价 kubectl describe po <pod> -n <ns>。"""
    if not pod:
        return _DETAIL_HELP
    v1 = _load_v1()
    if namespace:
        try:
            p = v1.read_namespaced_pod(name=pod, namespace=namespace)
        except ApiException as exc:
            if exc.status == 404:
                raise K8sQueryError(f"Pod 不存在: {namespace}/{pod}")
            raise K8sQueryError(f"查询 Pod 详情失败: {exc}")
        ns = namespace
    else:
        found, matches = find_pod(pod)
        if not found:
            names = "\n".join(f"  {n}/{m}" for n, m in matches[:10])
            more = f"\n  ...共 {len(matches)} 个，请指定具体名称或命名空间" if len(matches) > 10 else ""
            return f"匹配到多个 Pod，请指定具体名称：\n{names}{more}"
        ns, name = found
        try:
            p = v1.read_namespaced_pod(name=name, namespace=ns)
        except ApiException as exc:
            raise K8sQueryError(f"查询 Pod 详情失败: {exc}")

    meta, spec, status = p.metadata, p.spec, p.status
    lines = [f"【Pod 详情】{meta.name}（命名空间: {ns}）"]
    lines.append(f"状态: {status.phase or '?'} | 节点: {spec.node_name or '未调度'}")
    if status.pod_ip:
        lines.append(f"Pod IP: {status.pod_ip} | Host IP: {status.host_ip or '-'}")
    if meta.creation_timestamp:
        lines.append(f"创建时间: {meta.creation_timestamp}")
    if meta.uid:
        lines.append(f"UID: {meta.uid}")
    if meta.labels:
        lines.append("标签: " + ", ".join(f"{k}={v}" for k, v in meta.labels.items()))
    if meta.owner_references:
        lines.append("控制器: " + ", ".join(f"{r.kind}/{r.name}" for r in meta.owner_references))

    lines.append("")
    lines.append(f"容器 ({len(spec.containers)} 个):")
    cstatus = {c.name: c for c in (status.container_statuses or [])}
    for c in spec.containers:
        cs = cstatus.get(c.name)
        state = _container_state(cs) if cs else "?"
        ready = "就绪" if cs and cs.ready else "未就绪"
        restart = cs.restart_count if cs else 0
        req = c.resources.requests or {}
        lim = c.resources.limits or {}
        lines.append(f"  - {c.name}: {c.image}")
        lines.append(f"    状态: {state} | {ready} | 重启 {restart} 次")
        # Last State：上次退出的原因（CrashLoopBackOff 排查关键，对应 kubectl describe 的 Last State）
        if cs and cs.last_state:
            last = cs.last_state.terminated or cs.last_state.waiting
            if last:
                parts = [last.reason or "exit", str(last.exit_code or "")]
                if last.finished_at:
                    parts.append(f"于 {last.finished_at}")
                lines.append(f"    上次退出: {' '.join(parts)}")
        lines.append(
            f"    Requests: cpu {req.get('cpu', '-')} 内存 {req.get('memory', '-')}"
            f" | Limits: cpu {lim.get('cpu', '-')} 内存 {lim.get('memory', '-')}"
        )
        ports = [f"{pt.container_port}/{pt.protocol}" for pt in (c.ports or [])]
        if ports:
            lines.append(f"    端口: {', '.join(ports)}")

    events = _pod_events(ns, meta.name)
    if events:
        lines.append("")
        lines.append(f"事件 (最近 {len(events)} 条):")
        for e in events:
            msg = (e.message or "").replace("\n", " ")
            if len(msg) > 120:
                msg = msg[:120] + "…"
            lines.append(f"  - [{e.type}] {e.reason}: {msg}")
    return "\n".join(lines)


# ---------------- Pod 日志 ----------------
def find_pod(target):
    """
    根据用户输入定位 Pod。
    支持: 精确名 / 模糊名 / 命名空间/名字。
    返回: (namespace, name) 或 None（无法唯一定位）。
    若匹配到多个，返回 (None, matches_list)。
    """
    v1 = _load_v1()
    try:
        pods = v1.list_pod_for_all_namespaces().items
    except ApiException as exc:
        raise K8sQueryError(f"访问 Kubernetes API 失败: {exc}")

    target = (target or "").strip()
    if "/" in target:
        ns, name = target.split("/", 1)
        for pod in pods:
            if pod.metadata.namespace == ns and pod.metadata.name == name:
                return (ns, name), None
        raise K8sQueryError(f"未找到 Pod: {target}")

    # 支持 Pod UID 精确匹配（uid 形如 8f2d1c0e-xxxx-xxxx-xxxx-xxxxxxxxxxxx）
    for pod in pods:
        if pod.metadata.uid and pod.metadata.uid == target:
            return (pod.metadata.namespace, pod.metadata.name), None

    kw = target.lower()
    matched = [
        (p.metadata.namespace, p.metadata.name)
        for p in pods
        if kw in p.metadata.name.lower()
    ]
    if len(matched) == 0:
        raise K8sQueryError(f"未找到名称包含 “{target}” 的 Pod")
    if len(matched) == 1:
        return matched[0], None
    return None, matched


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _decode_log(log):
    """兼容不同版本 k8s 客户端的日志返回：bytes / 普通 str / 形如 b'...' 的 repr str。"""
    if isinstance(log, bytes):
        return log.decode("utf-8", errors="replace")
    if isinstance(log, str) and log.startswith(("b'", 'b"')):
        try:
            return ast.literal_eval(log).decode("utf-8", errors="replace")
        except (ValueError, SyntaxError):
            pass
    return log


def get_pod_log(namespace, name, tail_lines):
    v1 = _load_v1()
    tail = max(1, min(int(tail_lines or K8S_LOG_DEFAULT_LINES), K8S_LOG_MAX_LINES))
    try:
        log = v1.read_namespaced_pod_log(
            name=name, namespace=namespace, tail_lines=tail, timestamps=True
        )
    except ApiException as exc:
        if exc.status == 404:
            raise K8sQueryError(f"Pod 不存在: {namespace}/{name}")
        raise K8sQueryError(f"读取日志失败: {exc}")
    return _decode_log(log)


_LOG_HELP = (
    "查看日志需要指定 Pod，支持三种写法：\n"
    "1. 只看名称（自动在所有命名空间模糊查找）：\n"
    "   查看 nginx-abc-123 的日志\n"
    "2. 命名空间 + Pod 名（最精确）：\n"
    "   查看 default/nginx-abc-123 的日志\n"
    "3. Pod 唯一标识 UID：\n"
    "   日志 8f2d1c0e-xxxx-xxxx-xxxx-xxxxxxxxxxxx\n"
    "可先发「列出pod」查看有哪些 Pod（含命名空间）"
)


def _pod_uid(ns, name):
    """查询 Pod 的唯一标识 UID（拿不到时返回空串，不影响主流程）。"""
    v1 = _load_v1()
    try:
        return v1.read_namespaced_pod(name=name, namespace=ns).metadata.uid or ""
    except ApiException:
        return ""


def _save_log_file(ns, name, content):
    """把完整日志保存到服务器 logs/ 目录（容器内 /app/logs，已挂载宿主机 ./logs）。"""
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    fname = f"{ns}-{name}-{ts}.log"
    path = os.path.join(LOG_DIR, fname)
    os.makedirs(LOG_DIR, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path, fname, len(content.encode("utf-8"))


def pod_log_report(pod, lines=None):
    if not pod:
        return _LOG_HELP
    found, matches = find_pod(pod)
    if not found:
        names = "\n".join(f"  {ns}/{name}" for ns, name in matches[:10])
        more = f"\n  ...共 {len(matches)} 个，请给出更精确的 Pod 名" if len(matches) > 10 else ""
        return f"匹配到多个 Pod，请指定具体名称：\n{names}{more}"
    ns, name = found
    log = get_pod_log(ns, name, lines)
    lines_used = max(1, min(int(lines or K8S_LOG_DEFAULT_LINES), K8S_LOG_MAX_LINES))
    uid = _pod_uid(ns, name)
    info = f"命名空间: {ns} | Pod: {name}" + (f" | UID: {uid}" if uid else "")
    header = f"【Pod 日志】最近 {lines_used} 行"
    body = _ANSI_RE.sub("", log).rstrip()
    if not body.strip():
        body = "（该 Pod 当前无日志输出）"
    full = f"{header}\n{info}\n{body}"
    # “共 N 行”只统计日志正文（不含头部标题/命名空间 2 行，避免与输入的 N 行对不上）
    body_lines = body.count("\n") + (0 if body.endswith("\n") else 1)
    if len(full.encode("utf-8")) > MAX_REPLY_BYTES:
        # 日志过大：完整保存到文件，回复提供下载链接（nginx 已映射 /wecom-k8s-bot-logs/ -> 容器 /app/logs）
        path, fname, n_bytes = _save_log_file(ns, name, full)
        head = f"【Pod 日志】共 {body_lines} 行（{n_bytes} 字节），超过企微单条消息上限，已保存为日志文件："
        if LOG_DOWNLOAD_BASE_URL:
            url = f"{LOG_DOWNLOAD_BASE_URL}/wecom-k8s-bot-logs/{fname}"
            return (
                f"{head}\n"
                f"[点击下载日志文件]({url})\n"
                f"下载地址：{url}\n"
                f"命名空间: {ns} | Pod: {name}\n"
                f"提示：日志行数以容器实际输出为准（kubectl logs --tail 同理，日志轮转时可能不足请求行数）；"
                f"如想直接在会话里看，可减少行数，例如「查看 {name} 的日志 20 行」"
            )
        return (
            f"{head}\n"
            f"服务器路径：{path}\n"
            f"命名空间: {ns} | Pod: {name}\n"
            f"提示：未配置下载地址（LOG_DOWNLOAD_BASE_URL），请到服务器查看该文件；"
            f"或减少行数直接在会话里看，例如「查看 {name} 的日志 20 行」"
        )
    return full
