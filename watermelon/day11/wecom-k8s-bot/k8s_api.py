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
        def _pct_txt(v):
            return f"({v:.0f}%)" if v < 80 else _hl(f"({v:.0f}%)", "warning")

        lines.append(
            f"  {name}: CPU {cpu_u:.1f}/{cpu_a:.1f} 核 {_pct_txt(_pct(cpu_u, cpu_a))} | "
            f"内存 {mem_u:.1f}/{mem_a:.1f} Gi {_pct_txt(_pct(mem_u, mem_a))}"
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
    return f"**【命名空间列表】** 共 {len(names)} 个\n" + "\n".join(f"  {n}" for n in names)













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
        lines = [f"**【Pod 列表】** 命名空间 {namespace}，共 {len(items)} 个（格式：Pod名 [状态]）"
                 + (f"（匹配: {keyword}）" if kw else "")]
    else:
        lines = [f"**【Pod 列表】** 共 {len(items)} 个（格式：命名空间/Pod名 [状态]）"
                 + (f"（匹配: {keyword}）" if kw else "")]
    if not items:
        lines.append("没有找到匹配的 Pod")
    shown = items if not limit else items[:limit]
    for ns, name, phase in shown:
        p_txt = f"[{phase}]"
        if phase not in ("Running", "Succeeded", "Completed"):
            p_txt = f"[{_hl(phase, 'warning')}]"
        lines.append(f"  {ns}/{name} {p_txt}")
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
                try:
                    ns_pods = v1.list_namespaced_pod(namespace, watch=False).items
                except Exception:
                    ns_pods = []
                raise K8sQueryError(_pod_not_found_msg(pod, namespace, ns_pods))
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
    phase = status.phase or "?"
    phase_txt = phase if phase in ("Running", "Succeeded", "Completed") else _hl(phase, "warning")
    lines = [f"**【Pod 详情】** `{meta.name}`（`{ns}`）",
             f"状态: {phase_txt} | 节点: {spec.node_name or '未调度'}"]
    if status.pod_ip:
        lines.append(f"Pod IP: {status.pod_ip} | Host IP: {status.host_ip or '-'}")
    if meta.creation_timestamp:
        lines.append(f"创建时间: {meta.creation_timestamp}")
    if meta.uid:
        lines.append(f"UID: {meta.uid}")
    if meta.labels:
        labels = ", ".join(f"{k}={v}" for k, v in meta.labels.items())
        lines.append(f"标签: {_clip(labels, 120)}")
    if meta.owner_references:
        lines.append("控制器: " + ", ".join(f"{r.kind}/{r.name}" for r in meta.owner_references))

    lines.append("")
    lines.append(f"**容器 ({len(spec.containers)} 个):**")
    cstatus = {c.name: c for c in (status.container_statuses or [])}
    for c in spec.containers:
        cs = cstatus.get(c.name)
        state = _container_state(cs) if cs else "?"
        ready = "就绪" if cs and cs.ready else "未就绪"
        restart = cs.restart_count if cs else 0
        # 异常容器突出：Waiting(CrashLoop/ImagePull 等)/Terminated(exit!=0)/未就绪
        abnormal = False
        if cs and cs.state:
            w = cs.state.waiting
            tm = cs.state.terminated
            if (w and w.reason in _CRASH_REASONS) or (tm and (tm.exit_code or 0) != 0):
                abnormal = True
        if cs and not cs.ready:
            abnormal = True
        st_txt = _hl(state, "warning") if abnormal else state
        rd_txt = _hl("未就绪", "warning") if (cs and not cs.ready) else ready
        rst_txt = _hl(str(restart), "warning") if restart else str(restart)
        req = c.resources.requests or {}
        lim = c.resources.limits or {}
        lines.append(f"  - **{c.name}**: {_clip(c.image, 90)}")
        lines.append(f"    状态: {st_txt} | {rd_txt} | 重启 {rst_txt} 次")
        # Last State：上次退出的原因（CrashLoopBackOff 排查关键，对应 kubectl describe 的 Last State）
        if cs and cs.last_state:
            last = cs.last_state.terminated or cs.last_state.waiting
            if last:
                parts = [last.reason or "exit", str(last.exit_code or "")]
                if last.finished_at:
                    parts.append(f"于 {last.finished_at}")
                lines.append(f"    **上次退出: {_hl(' '.join(parts), 'warning')}**")
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
        lines.append(f"**事件 (最近 {len(events)} 条):**")
        for e in events:
            msg = _clip(e.message or "", 100)
            ev = f"[{e.type}] {e.reason}: {msg}"
            if (e.type or "").lower() == "warning":
                ev = _hl(ev, "warning")
            lines.append(f"  - {ev}")
    return "\n".join(lines)


def pod_last_state_report(pod, namespace=None):
    """查看 Pod 上次退出/重启原因（Last State）+ 重启次数 + 最近事件，手机端精简输出。"""
    if not pod:
        return "查看上次退出原因需要指定 Pod，例如：查看 <pod名> 的上次退出原因"
    v1 = _load_v1()
    if namespace:
        try:
            p = v1.read_namespaced_pod(name=pod, namespace=namespace)
        except ApiException as exc:
            if exc.status == 404:
                try:
                    ns_pods = v1.list_namespaced_pod(namespace, watch=False).items
                except Exception:
                    ns_pods = []
                raise K8sQueryError(_pod_not_found_msg(pod, namespace, ns_pods))
            raise K8sQueryError(f"查询 Pod 失败: {exc}")
        ns = namespace
        name = pod
    else:
        found, matches = find_pod(pod)
        if not found:
            names = "\n".join(f"  {n}/{m}" for n, m in matches[:10])
            more = f"\n  ...共 {len(matches)} 个，请指定具体名称或命名空间" if len(matches) > 10 else ""
            return f"匹配到多个 Pod，请指定具体名称：\n{names}{more}"
        ns, name = found

    p = v1.read_namespaced_pod(name=name, namespace=ns)
    lines = [f"**🔍 Pod 退出/重启原因** `{ns}/{name}`"]
    cs_status = p.status.container_statuses or []
    if not cs_status:
        lines.append("（该 Pod 暂无容器状态信息）")
        return "\n".join(lines)
    has_last = False
    for cs in cs_status:
        restart = cs.restart_count or 0
        rst_txt = _hl(str(restart), "warning") if restart else "0"
        lines.append(f"**容器 {cs.name}** | 重启 {rst_txt} 次")
        if cs.last_state:
            tm = cs.last_state.terminated
            if tm:
                has_last = True
                t = f"**上次退出: {_hl(tm.reason or 'Exited', 'warning')}**（exit={tm.exit_code}）"
                if tm.finished_at:
                    t += f" 于 {tm.finished_at}"
                lines.append(t)
                msg = _clip(tm.message or "", 160)
                if msg:
                    lines.append(f"  原因: {msg}")
            elif cs.last_state.waiting:
                has_last = True
                w = cs.last_state.waiting
                t = f"**上次状态: {_hl(w.reason or 'Waiting', 'warning')}**"
                msg = _clip(w.message or "", 160)
                if msg:
                    t += f"\n  原因: {msg}"
                lines.append(t)
        if not (cs.last_state and (cs.last_state.terminated or cs.last_state.waiting)):
            lines.append("上次退出: 无记录（容器未退出过或记录已滚动）")
        lines.append(f"当前状态: {_container_state(cs)}")
    evs = _pod_events(ns, name, limit=6)
    if evs:
        lines.append("")
        lines.append("**最近事件:**")
        for e in evs:
            ev = f"[{e.type}] {e.reason}: {_clip(e.message or '', 100)}"
            if (e.type or "").lower() == "warning":
                ev = _hl(ev, "warning")
            lines.append(f"  · {ev}")
    if not has_last:
        lines.append("")
        lines.append("提示：容器均无上次退出记录，说明未发生过退出/重启（或信息已被覆盖）；"
                     "若业务异常可查看「<pod名> 的错误日志」")
    return "\n".join(lines)


# ---------------- Pod 日志 ----------------
def _suggest_pod_names(target, pods, limit=6):
    """基于相似度给出相近 Pod 名引导（拼写错误/名称不全时用）。"""
    from difflib import SequenceMatcher
    target = (target or "").strip().lower()
    if not target:
        return []
    scored = []
    for p in pods:
        meta = p.metadata
        if not meta or not meta.name:
            continue
        name = meta.name.lower()
        ns = meta.namespace or ""
        ratio = SequenceMatcher(None, target, name).ratio()
        if target in name:
            ratio = max(ratio, 0.75)
        if name.startswith(target):
            ratio = max(ratio, 0.85)
        scored.append((ratio, ns, meta.name))
    scored.sort(key=lambda x: -x[0])
    out = []
    for ratio, ns, name in scored:
        if ratio < 0.35:
            break
        out.append(f"{ns}/{name}" if ns else name)
        if len(out) >= limit:
            break
    return out


def _pod_not_found_msg(target, namespace=None, pods=None, limit=6):
    """构造"未找到 Pod + 相近名称引导"的提示。"""
    if pods is None:
        try:
            v1 = _load_v1()
            pods = v1.list_pod_for_all_namespaces(watch=False).items
        except Exception:
            pods = []
    hints = _suggest_pod_names(target, pods, limit)
    where = f"（命名空间 {namespace}）" if namespace else ""
    if hints:
        return f"未找到 Pod “{target}”{where}，相近的有：\n" + "\n".join(f"  · {h}" for h in hints)
    return f"未找到 Pod “{target}”{where}"


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
        raise K8sQueryError(_pod_not_found_msg(target, pods=pods))
    if len(matched) == 1:
        return matched[0], None
    return None, matched


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _clip(text, n=100):
    """手机端友好：单行超长截断（换行压缩为空格）。"""
    text = (text or "").replace("\n", " ").strip()
    return text if len(text) <= n else text[:n] + "…"


def _hl(text, color="warning"):
    """企微 markdown 彩色字体：info=蓝 / comment=灰 / warning=橙。"""
    return f'<font color="{color}">{text}</font>'


def _fmt_log_line(ln):
    """日志行手机端展示：错误行自动标 🚨 橙色突出，超长截断。"""
    if _ERROR_RE.search(ln):
        return _hl("🚨 " + _clip(ln, 100))
    return _clip(ln, 100)


# 容器异常状态（对应 kubectl describe 里的 Waiting/Terminated reason）
_CRASH_REASONS = {
    "CrashLoopBackOff", "ImagePullBackOff", "ErrImagePull", "InvalidImageName",
    "RunContainerError", "CreateContainerError", "CreateContainerConfigError",
    "Error", "OOMKilled", "ContainerCannotRun",
}


def _apps_v1():
    try:
        config.load_incluster_config()
    except Exception:
        config.load_kube_config(config_file=KUBECONFIG)
    return client.AppsV1Api()


def deploy_log_report(deploy, namespace=None, lines=None, all_pods=False):
    """查看 Deployment 控制器的日志。
    all_pods=False：取最新一个 Pod 的日志；
    all_pods=True：混合输出该 Deployment 下所有 Pod 的日志（每条标注 Pod 名）。"""
    if not deploy:
        return "查看 Deployment 日志需要指定名称，例如：\n  查看 deployment coffee-mp 的日志\n  日志 deploy/coffee-mp 后200行"
    apps = _apps_v1()
    v1 = _load_v1()
    ns = namespace
    if not ns:
        # 跨命名空间查找该 Deployment
        items = apps.list_deployment_for_all_namespaces(watch=False).items
        found = [d for d in items if d.metadata.name == deploy]
        if len(found) == 0:
            fuzzy = [d.metadata.name for d in items if deploy.lower() in d.metadata.name.lower()][:10]
            if fuzzy:
                return f"未找到 Deployment {deploy}，相近的有：\n  " + "\n  ".join(fuzzy)
            return f"未找到 Deployment {deploy}（集群中不存在该名称）"
        if len(found) > 1:
            return "找到多个同名 Deployment，请指定命名空间：\n  " + "\n  ".join(
                f"{d.metadata.namespace}/{d.metadata.name}" for d in found)
        d = found[0]
        ns = d.metadata.namespace
    else:
        try:
            d = apps.read_namespaced_deployment(name=deploy, namespace=ns)
        except ApiException as exc:
            raise K8sQueryError(f"查询 Deployment 失败: {exc}")
    selector = d.spec.selector
    labels = (selector.match_labels or {}) if selector else {}
    sel = ",".join(f"{k}={v}" for k, v in labels.items())
    if not sel:
        return f"Deployment {deploy}（{ns}）没有可用的标签选择器，无法定位 Pod"
    pods = v1.list_namespaced_pod(ns, label_selector=sel, watch=False).items
    if not pods:
        return f"Deployment {deploy}（{ns}）当前没有任何 Pod"

    # 按创建时间倒序取最新 Pod；时间为空/naive 时统一转 UTC，避免类型比较报错
    _EPOCH = datetime.datetime(1970, 1, 1, tzinfo=datetime.timezone.utc)

    def _pod_ct(p):
        ts = p.metadata.creation_timestamp
        if ts is None:
            return _EPOCH
        return ts if ts.tzinfo else ts.replace(tzinfo=datetime.timezone.utc)

    pods.sort(key=_pod_ct, reverse=True)

    def _fmt_log(ns_name, pod_name, tail):
        log = get_pod_log(ns_name, pod_name, tail)
        body = _ANSI_RE.sub("", log).rstrip()
        if not body.strip():
            return "（该 Pod 当前无日志输出）", "（该 Pod 当前无日志输出）"
        return "\n".join(_fmt_log_line(ln) for ln in body.splitlines()), body

    lines_used = max(1, min(int(lines or K8S_LOG_DEFAULT_LINES), K8S_LOG_MAX_LINES))
    if not all_pods:
        # 默认：只取最新一个 Pod 的日志
        target = pods[0]
        name = target.metadata.name
        shown, raw_body = _fmt_log(ns, name, lines_used)
        multi = f"（该 Deployment 共 {len(pods)} 个 Pod，已取最新：{name}）\n" if len(pods) > 1 else ""
        full = f"**【Deployment 日志】** {deploy}（{ns}）\n{multi}命名空间: {ns} | Pod: {name}\n{shown}"
        save_full = f"【Deployment 日志】{deploy}（{ns}）\n{multi}命名空间: {ns} | Pod: {name}\n{raw_body}"
        if len(full.encode("utf-8")) > MAX_REPLY_BYTES:
            path, fname, n_bytes = _save_log_file(ns, name, save_full)
            head = f"**【Deployment 日志】** 共 {raw_body.count(chr(10)) + 1} 行（{n_bytes} 字节），超过企微单条消息上限，已保存为日志文件："
            if LOG_DOWNLOAD_BASE_URL:
                url = f"{LOG_DOWNLOAD_BASE_URL}/wecom-k8s-bot-logs/{fname}"
                return f"{head}\n[📥 点击下载日志文件]({url})\n下载地址：{url}\n命名空间: {ns} | Pod: {name}"
            return f"{head}\n服务器路径：{path}\n命名空间: {ns} | Pod: {name}"
        return full

    # 多 Pod 混合输出：所有 Pod 的日志按时间倒序拼接，每条标注 Pod 名
    parts = []
    raw_parts = []
    for p in pods:
        pname = p.metadata.name
        shown, raw_body = _fmt_log(ns, pname, lines_used)
        parts.append(f"----- [{pname}] -----\n{shown}")
        raw_parts.append(f"----- [{pname}] -----\n{raw_body}")
    merged = "\n".join(parts)
    merged_raw = "\n".join(raw_parts)
    total_lines = sum(pb.count(chr(10)) + 1 for pb in parts)
    head = f"**【Deployment 日志】** {deploy}（{ns}）\n共 {len(pods)} 个 Pod（混合输出，每条标注 Pod 名），每个 Pod 取最近 {lines_used} 行\n"
    full = head + merged
    save_full = f"【Deployment 日志】{deploy}（{ns}）\n共 {len(pods)} 个 Pod（混合输出），每个 Pod 取最近 {lines_used} 行\n" + merged_raw
    if len(full.encode("utf-8")) > MAX_REPLY_BYTES:
        path, fname, n_bytes = _save_log_file(ns, deploy, save_full)
        h2 = f"**【Deployment 日志】** 共 {len(pods)} 个 Pod、{total_lines} 行（{n_bytes} 字节），超过企微单条消息上限，已保存为日志文件："
        if LOG_DOWNLOAD_BASE_URL:
            url = f"{LOG_DOWNLOAD_BASE_URL}/wecom-k8s-bot-logs/{fname}"
            return f"{h2}\n[📥 点击下载日志文件]({url})\n下载地址：{url}\n命名空间: {ns}"
        return f"{h2}\n服务器路径：{path}\n命名空间: {ns}"
    return full


# ---------------- 通用只读查询（LLM 驱动的任意 K8s 资源查询） ----------------
# 资源别名 → K8s 资源类型（kind）
_QUERY_RESOURCES = {
    "pod": "Pod", "po": "Pod",
    "node": "Node", "no": "Node",
    "service": "Service", "svc": "Service", "services": "Service",
    "endpoint": "Endpoints", "endpoints": "Endpoints", "ep": "Endpoints",
    "configmap": "ConfigMap", "configmaps": "ConfigMap", "cm": "ConfigMap",
    "secret": "Secret", "secrets": "Secret",
    "persistentvolumeclaim": "PersistentVolumeClaim", "pvc": "PersistentVolumeClaim", "pvcs": "PersistentVolumeClaim",
    "persistentvolume": "PersistentVolume", "pv": "PersistentVolume", "pvs": "PersistentVolume",
    "namespace": "Namespace", "ns": "Namespace", "namespaces": "Namespace",
    "serviceaccount": "ServiceAccount", "sa": "ServiceAccount",
    "event": "Event", "events": "Event",
    "resourcequota": "ResourceQuota", "quota": "ResourceQuota", "quotas": "ResourceQuota",
    "limitrange": "LimitRange",
    "deployment": "Deployment", "deploy": "Deployment", "deployments": "Deployment",
    "replicaset": "ReplicaSet", "rs": "ReplicaSet",
    "statefulset": "StatefulSet", "sts": "StatefulSet",
    "daemonset": "DaemonSet", "ds": "DaemonSet",
    "job": "Job", "jobs": "Job",
    "cronjob": "CronJob", "cj": "CronJob", "cronjobs": "CronJob",
    "ingress": "Ingress", "ing": "Ingress", "ingresses": "Ingress",
    "networkpolicy": "NetworkPolicy", "netpol": "NetworkPolicy",
    "hpa": "HorizontalPodAutoscaler", "horizontalpodautoscaler": "HorizontalPodAutoscaler",
}

_QUERY_LIST_ALL = {
    "Pod": "list_pod_for_all_namespaces", "Node": "list_node",
    "Service": "list_service_for_all_namespaces", "Endpoints": "list_endpoints_for_all_namespaces",
    "ConfigMap": "list_config_map_for_all_namespaces", "Secret": "list_secret_for_all_namespaces",
    "PersistentVolumeClaim": "list_persistent_volume_claim_for_all_namespaces",
    "PersistentVolume": "list_persistent_volume", "Namespace": "list_namespace",
    "ServiceAccount": "list_service_account_for_all_namespaces",
    "Event": "list_event_for_all_namespaces",
    "ResourceQuota": "list_resource_quota_for_all_namespaces",
    "LimitRange": "list_limit_range_for_all_namespaces",
    "Deployment": "list_deployment_for_all_namespaces", "ReplicaSet": "list_replica_set_for_all_namespaces",
    "StatefulSet": "list_stateful_set_for_all_namespaces", "DaemonSet": "list_daemon_set_for_all_namespaces",
    "Job": "list_job_for_all_namespaces", "CronJob": "list_cron_job_for_all_namespaces",
    "Ingress": "list_ingress_for_all_namespaces", "NetworkPolicy": "list_network_policy_for_all_namespaces",
    "HorizontalPodAutoscaler": "list_horizontal_pod_autoscaler_for_all_namespaces",
}

_QUERY_LIST_NS = {
    "Pod": "list_namespaced_pod", "Service": "list_namespaced_service",
    "Endpoints": "list_namespaced_endpoints", "ConfigMap": "list_namespaced_config_map",
    "Secret": "list_namespaced_secret", "PersistentVolumeClaim": "list_namespaced_persistent_volume_claim",
    "ServiceAccount": "list_namespaced_service_account", "Event": "list_namespaced_event",
    "ResourceQuota": "list_namespaced_resource_quota", "LimitRange": "list_namespaced_limit_range",
    "Deployment": "list_namespaced_deployment", "ReplicaSet": "list_namespaced_replica_set",
    "StatefulSet": "list_namespaced_stateful_set", "DaemonSet": "list_namespaced_daemon_set",
    "Job": "list_namespaced_job", "CronJob": "list_namespaced_cron_job",
    "Ingress": "list_namespaced_ingress", "NetworkPolicy": "list_namespaced_network_policy",
    "HorizontalPodAutoscaler": "list_namespaced_horizontal_pod_autoscaler",
}

_QUERY_READ = {
    "Pod": "read_namespaced_pod", "Node": "read_node",
    "Service": "read_namespaced_service", "Endpoints": "read_namespaced_endpoints",
    "ConfigMap": "read_namespaced_config_map", "Secret": "read_namespaced_secret",
    "PersistentVolumeClaim": "read_namespaced_persistent_volume_claim",
    "PersistentVolume": "read_persistent_volume", "Namespace": "read_namespace",
    "ServiceAccount": "read_namespaced_service_account",
    "ResourceQuota": "read_namespaced_resource_quota", "LimitRange": "read_namespaced_limit_range",
    "Deployment": "read_namespaced_deployment", "ReplicaSet": "read_namespaced_replica_set",
    "StatefulSet": "read_namespaced_stateful_set", "DaemonSet": "read_namespaced_daemon_set",
    "Job": "read_namespaced_job", "CronJob": "read_namespaced_cron_job",
    "Ingress": "read_namespaced_ingress", "NetworkPolicy": "read_namespaced_network_policy",
    "HorizontalPodAutoscaler": "read_namespaced_horizontal_pod_autoscaler",
}


def _api_for_kind(kind):
    # 与 _load_v1/_apps_v1 一致：先加载集群配置，再构造对应客户端
    try:
        config.load_incluster_config()
    except Exception:
        config.load_kube_config(config_file=KUBECONFIG)
    if kind in ("Deployment", "ReplicaSet", "StatefulSet", "DaemonSet"):
        return client.AppsV1Api()
    if kind in ("Job", "CronJob"):
        return client.BatchV1Api()
    if kind in ("Ingress", "NetworkPolicy"):
        return client.NetworkingV1Api()
    if kind == "HorizontalPodAutoscaler":
        return client.AutoscalingV1Api()
    return client.CoreV1Api()


def _query_summary(kind, obj):
    """每种资源的单行摘要。"""
    meta = obj.metadata
    st = obj.status
    name = meta.name
    if kind == "Pod":
        cs = st.container_statuses or [] if st else []
        ready = sum(1 for c in cs if c.ready)
        restarts = sum(c.restart_count or 0 for c in cs)
        ph = st.phase if st else "?"
        ph_txt = ph if ph in ("Running", "Succeeded", "Completed") else _hl(ph, "warning")
        return _clip(f"{name}  [{ph_txt}]  {ready}/{len(cs)} 重启{restarts}次", 110)
    if kind == "Node":
        cond = next((c.status for c in (st.conditions or []) if c.type == "Ready"), "?")
        cond_txt = cond if cond == "True" else _hl(cond, "warning")
        taints = ",".join(t.key for t in (obj.spec.taints or [])) or "-"
        return _clip(f"{name}  [Ready={cond_txt}]  taints={taints}", 110)
    if kind in ("Deployment", "StatefulSet", "DaemonSet", "ReplicaSet"):
        return f"{name}  ready {(st.ready_replicas if st else None) or 0}/{(st.replicas if st else None) or 0}"
    if kind == "Service":
        ports = ",".join(f"{p.port}/{p.protocol}" for p in (obj.spec.ports or [])) or "-"
        return f"{name}  type={obj.spec.type or 'ClusterIP'}  clusterIP={obj.spec.cluster_ip or '-'}  ports={ports}"
    if kind == "Endpoints":
        addrs = sum(len(s.addresses or []) for s in (st.subsets or [])) if st else 0
        return f"{name}  后端地址 {addrs} 个"
    if kind == "PersistentVolumeClaim":
        cap = (st.capacity or {}).get("storage", "-") if st else "-"
        return f"{name}  [{st.phase if st else '?'}]  {cap}  accessModes={','.join(obj.spec.access_modes or []) or '-'}"
    if kind == "PersistentVolume":
        cap = (st.capacity or {}).get("storage", "-") if st else "-"
        return f"{name}  [{st.phase if st else '?'}]  {cap}  {obj.spec.reclaim_policy or '-'}"
    if kind == "Namespace":
        return f"{name}  [{st.phase if st else '?'}]"
    if kind in ("ConfigMap", "Secret"):
        return f"{name}  {len(obj.data or {})} 个键"
    if kind == "Ingress":
        hosts = ";".join(r.host for r in (obj.spec.rules or []) if r.host) or "-"
        return f"{name}  hosts={hosts}"
    if kind == "Job":
        return f"{name}  active={(st.active if st else None) or 0}  succeeded={(st.succeeded if st else None) or 0}  failed={(st.failed if st else None) or 0}"
    if kind == "CronJob":
        return f"{name}  schedule={obj.spec.schedule or '-'}  suspend={bool(obj.spec.suspend)}"
    if kind == "Event":
        ts = st.last_timestamp or st.event_time or meta.creation_timestamp if st else meta.creation_timestamp
        io_ = obj.involved_object
        return f"[{ts}] {obj.type} {obj.reason} {io_.kind if io_ else '?'}/{io_.name if io_ else '?'}: {(obj.message or '')[:90]}"
    if kind == "HorizontalPodAutoscaler":
        return f"{name}  min={obj.spec.min_replicas}  max={obj.spec.max_replicas}  current={(st.current_replicas if st else None) or 0}"
    if kind in ("ResourceQuota", "LimitRange", "NetworkPolicy", "ServiceAccount"):
        return name
    return name


def _recent_events(kind, name, namespace, limit=8):
    """查询某对象最近相关事件。"""
    v1 = _load_v1()
    try:
        if namespace:
            evs = v1.list_namespaced_event(namespace, watch=False).items
        else:
            evs = v1.list_event_for_all_namespaces(watch=False).items
    except Exception:
        return []
    out = []
    for e in evs:
        io_ = e.involved_object or None
        if io_ and io_.name == name and (io_.kind or "").lower() == kind.lower():
            ts = (e.last_timestamp or e.event_time or e.metadata.creation_timestamp)
            ev = f"[{ts}] {e.type} {e.reason}: {_clip(e.message or '', 100)}"
            if (e.type or "").lower() == "warning":
                ev = _hl(ev, "warning")
            out.append(ev)
    try:
        out.sort(key=lambda s: s, reverse=True)
    except Exception:
        pass
    return out[:limit]


def _query_single_detail(kind, obj, namespace):
    """单个资源的详情：基本信息 + 状态 + 相关事件。"""
    meta = obj.metadata
    st = obj.status
    lines = [f"【{kind} 详情】{meta.name}"]
    if meta.namespace:
        lines.append(f"命名空间: {meta.namespace}")
    lines.append(f"创建时间: {meta.creation_timestamp or '-'}")
    labels = meta.labels or {}
    if labels:
        lines.append("标签: " + _clip(", ".join(f"{k}={v}" for k, v in labels.items()), 120))
    if st is not None:
        for f in ("phase", "replicas", "ready_replicas", "available_replicas",
                  "updated_replicas", "desired_replicas", "current_replicas",
                  "observed_generation", "succeeded", "failed", "active"):
            v = getattr(st, f, None)
            if v is not None:
                lines.append(f"{f.replace('_', ' ')}: {v}")
        conds = st.conditions or []
        if conds:
            lines.append("状态条件:")
            for c in conds[:10]:
                lines.append(f"  · {c.type}={c.status}  reason={c.reason or '-'}  msg={(c.message or '')[:100]}")
    evs = _recent_events(kind, meta.name, meta.namespace or namespace)
    if evs:
        lines.append("最近事件:")
        lines.extend("  · " + e for e in evs)
    return "\n".join(lines)


def k8s_query_report(resource, action="list", name=None, namespace=None, label=None):
    """通用只读查询：LLM 识别出任意 K8s 资源查询时执行。
    仅支持 list / get / describe（只读），拒绝任何写操作。"""
    if not resource:
        return ("请告诉我你想查什么资源，例如：service、pvc、ingress、cronjob、node 等。\n"
                "支持的资源：Pod / Node / Service / Endpoints / ConfigMap / Secret / PVC / PV / Namespace / "
                "Deployment / ReplicaSet / StatefulSet / DaemonSet / Job / CronJob / Ingress / "
                "NetworkPolicy / HPA / Event / ResourceQuota / LimitRange 等")
    kind = _QUERY_RESOURCES.get(str(resource).strip().lower())
    if not kind:
        return (f"暂不支持查询资源类型 {resource}。目前支持：\n"
                "  Pod / Node / Service / Endpoints / ConfigMap / Secret\n"
                "  PVC / PV / Namespace / Deployment / ReplicaSet / StatefulSet / DaemonSet\n"
                "  Job / CronJob / Ingress / NetworkPolicy / HPA / Event / ResourceQuota 等")
    action = (action or "list").lower()
    if action not in ("list", "get", "describe"):
        return (
            f"⚠️ 不支持的操作 {action}。我是只读查询助手，无法执行重启/删除/修改/扩容等变更操作，"
            "只支持查看类查询（list/get/describe）。"
        )
    # 防误判：LLM 把资源类型本身当名字（如 describe node 节点）时忽略
    if name and str(name).strip().lower() == str(resource).strip().lower():
        name = None
    api = _api_for_kind(kind)
    try:
        if name and action in ("get", "describe"):
            obj = _read_query_obj(api, kind, name, namespace)
            return _query_single_detail(kind, obj, namespace)
        items = _list_query_objs(api, kind, namespace, label)
    except ApiException as exc:
        if exc.status == 404:
            return f"未找到 {kind} {name or ''}（命名空间: {namespace or '全部'}）"
        raise K8sQueryError(f"查询 {kind} 失败: {exc}")
    if not items:
        where = f"命名空间 {namespace}" if namespace else "全部命名空间"
        return f"未找到任何 {kind}（{where}）"
    lines = [_query_summary(kind, it) for it in items]
    total = len(lines)
    head = f"【{kind} 列表】共 {total} 个" + (f"（命名空间 {namespace}）" if namespace else "")
    out = head + "\n" + "\n".join("  · " + s for s in lines[:30])
    if total > 30:
        out += f"\n共 {total} 个，仅显示前 30 个；可指定命名空间缩小范围，或发「查看 pod 列表」查看具体 Pod"
    if len(out.encode("utf-8")) > MAX_REPLY_BYTES:
        out = out[: int(MAX_REPLY_BYTES * 0.9)]
    return out


def _list_query_objs(api, kind, namespace, label=None):
    kw = {"watch": False}
    if label:
        kw["label_selector"] = label
    if namespace:
        return getattr(api, _QUERY_LIST_NS[kind])(namespace, **kw).items
    return getattr(api, _QUERY_LIST_ALL[kind])(**kw).items


def _read_query_obj(api, kind, name, namespace):
    m = _QUERY_READ[kind]
    if namespace and m in ("read_namespaced_pod", "read_namespaced_service",
                           "read_namespaced_endpoints", "read_namespaced_config_map",
                           "read_namespaced_secret", "read_namespaced_persistent_volume_claim",
                           "read_namespaced_service_account", "read_namespaced_resource_quota",
                           "read_namespaced_limit_range", "read_namespaced_deployment",
                           "read_namespaced_replica_set", "read_namespaced_stateful_set",
                           "read_namespaced_daemon_set", "read_namespaced_job",
                           "read_namespaced_cron_job", "read_namespaced_ingress",
                           "read_namespaced_network_policy",
                           "read_namespaced_horizontal_pod_autoscaler"):
        return getattr(api, m)(name, namespace)
    return getattr(api, m)(name)


def crash_pods_report(limit=30):
    """列出集群中处于异常/崩溃状态的 Pod（跨命名空间）。"""
    v1 = _load_v1()
    pods = v1.list_pod_for_all_namespaces(watch=False).items
    total = len(pods)
    bad = []
    for p in pods:
        st = p.status
        phase = st.phase or "?"
        reason = ""
        restart = 0
        for cs in st.container_statuses or []:
            restart += cs.restart_count or 0
            if not reason:
                w = cs.state.waiting if cs.state and cs.state.waiting else None
                tm = cs.state.terminated if cs.state and cs.state.terminated else None
                if w and w.reason in _CRASH_REASONS:
                    reason = (w.message or w.reason)[:80] or w.reason
                elif tm and (tm.exit_code or 0) != 0:
                    reason = f"{tm.reason or 'Exited'} (exit={tm.exit_code})"
        if phase in ("Running", "Succeeded", "Completed"):
            # Running/Succeeded 也要看容器是否崩溃过（如 CrashLoopBackOff）
            if not reason:
                continue
        elif phase in ("Pending", "Failed", "Unknown", "?"):
            if not reason:
                reason = f"phase={phase}"
        else:
            continue
        bad.append((p.metadata.namespace, p.metadata.name, phase, restart, reason))
    if not bad:
        return f"**【Pod 崩溃排查】** 集群共 {total} 个 Pod，均正常（无 CrashLoop/Error/镜像拉取失败等异常）"
    bad.sort(key=lambda x: (-x[3], x[0], x[1]))  # 重启次数多优先
    shown = bad[:limit]
    lines = [f"**【Pod 崩溃排查】** 共 {_hl(str(len(bad)), 'warning')} 个异常 Pod（集群共 {total} 个）："]
    for i, (ns, name, phase, restart, reason) in enumerate(shown, 1):
        lines.append(f"{i}. **{ns}/{name}**")
        lines.append(f"   状态: {phase} | 重启 {restart} 次 | 原因: {_hl(_clip(reason, 90), 'warning')}")
    if len(bad) > limit:
        lines.append(f"…还有 {len(bad) - limit} 个未显示（可稍后重试或缩小范围）")
    lines.append("提示：想看某个 Pod 的详细退出原因，发「查看 <pod名> 的详情」；想筛错误日志发「查看 <pod名> 的错误日志」")
    return "\n".join(lines)




# ---------------- 集群巡检（异常 Pod 深度分析） ----------------
def _abnormal_pods():
    """扫描全部命名空间，返回异常 Pod 列表（含状态/重启/原因/节点）。"""
    v1 = _load_v1()
    pods = v1.list_pod_for_all_namespaces(watch=False).items
    bad = []
    for p in pods:
        st = p.status
        phase = st.phase or "?"
        reason = ""
        restart = 0
        for cs in st.container_statuses or []:
            restart += cs.restart_count or 0
            if not reason:
                w = cs.state.waiting if cs.state and cs.state.waiting else None
                tm = cs.state.terminated if cs.state and cs.state.terminated else None
                if w and w.reason in _CRASH_REASONS:
                    reason = (w.message or w.reason)[:100] or w.reason
                elif tm and (tm.exit_code or 0) != 0:
                    reason = f"{tm.reason or 'Exited'} (exit={tm.exit_code})"
        if phase in ("Running", "Succeeded", "Completed"):
            if not reason:
                continue
        elif phase in ("Pending", "Failed", "Unknown", "?"):
            if not reason:
                reason = f"phase={phase}"
        else:
            continue
        bad.append({
            "ns": p.metadata.namespace,
            "name": p.metadata.name,
            "phase": phase,
            "restart": restart,
            "reason": reason,
            "node": (p.spec.node_name or "") if p.spec else "",
            "pod": p,
        })
    return len(pods), bad


def _pod_error_lines(ns, name, tail=100, max_show=3):
    """读 Pod 最近日志并筛出错误行（读不到时返回空）。"""
    try:
        log = get_pod_log(ns, name, tail)
    except K8sQueryError:
        return None
    body = _ANSI_RE.sub("", log)
    errs = [ln.strip() for ln in body.splitlines() if _ERROR_RE.search(ln)]
    return errs[:max_show]


def _recent_events_brief(ns, pod_name, limit=6):
    """Pod 最近事件（reason: message），按时间倒序。"""
    v1 = _load_v1()
    try:
        evs = v1.list_namespaced_event(
            ns, field_selector="involvedObject.name=" + pod_name, watch=False
        ).items
    except ApiException:
        return []
    _EPOCH = datetime.datetime(1970, 1, 1, tzinfo=datetime.timezone.utc)

    def _ts(e):
        ts = e.last_timestamp or e.event_time
        if not ts and e.metadata:
            ts = e.metadata.creation_timestamp
        return ts if ts else _EPOCH

    evs.sort(key=_ts, reverse=True)
    out = []
    for e in evs[:limit]:
        msg = (e.message or "").replace("\n", " ")[:70]
        out.append(f"{e.reason}: {msg}")
    return out


def _controller_change_brief(ns, pod):
    """定位 Pod 所属控制器，返回 (控制器描述, 最近变更描述)。"""
    owners = (pod.metadata.owner_references or []) if pod.metadata else []
    if not owners:
        return None, None
    owner = owners[0]
    kind, name = owner.kind, owner.name
    try:
        apps = _apps_v1()
        if kind == "ReplicaSet":
            rs = apps.read_namespaced_replica_set(name=name, namespace=ns)
            rs_owners = rs.metadata.owner_references or []
            if rs_owners and rs_owners[0].kind == "Deployment":
                dname = rs_owners[0].name
                d = apps.read_namespaced_deployment(name=dname, namespace=ns)
                drev = (d.metadata.annotations or {}).get("deployment.kubernetes.io/revision", "?")
                sel = d.spec.selector
                labels = (sel.match_labels or {}) if sel else {}
                sel_s = ",".join(f"{k}={v}" for k, v in labels.items())
                rss = apps.list_namespaced_replica_set(ns, label_selector=sel_s, watch=False).items

                def _rev(r):
                    try:
                        return int((r.metadata.annotations or {}).get("deployment.kubernetes.io/revision", "0") or 0)
                    except (TypeError, ValueError):
                        return 0

                rss.sort(key=_rev, reverse=True)
                if rss:
                    latest = rss[0]
                    img = "?"
                    if latest.spec and latest.spec.template and latest.spec.template.spec and latest.spec.template.spec.containers:
                        img = latest.spec.template.spec.containers[0].image or "?"
                    ct = latest.metadata.creation_timestamp
                    ct_s = ct.strftime("%m-%d %H:%M") if ct else "?"
                    return (f"Deployment {dname} (revision {drev})",
                            f"最新版本 {latest.metadata.name} 创建于 {ct_s}，镜像 {img}")
                return f"Deployment {dname} (revision {drev})", None
            rev = (rs.metadata.annotations or {}).get("deployment.kubernetes.io/revision", "?")
            return f"ReplicaSet {name} (revision {rev})", None
        if kind in ("StatefulSet", "DaemonSet", "Job", "CronJob"):
            return f"{kind} {name}", None
        return f"{kind} {name}", None
    except ApiException:
        return None, None


def _node_resources_brief():
    """全部节点 CPU/内存占用简表，返回 [(节点名, cpu%, mem%, 是否高占用)]；不可用返回 None。"""
    try:
        v1 = _load_v1()
        nodes = v1.list_node(watch=False).items
        metrics = _custom_api().list_cluster_custom_object(
            group="metrics.k8s.io", version="v1beta1", plural="nodes"
        )
        usage = {it["metadata"]["name"]: it.get("usage", {}) for it in metrics.get("items", [])}
    except ApiException:
        return None
    out = []
    for n in nodes:
        alloc = n.status.allocatable or {}
        cpu_a = cpu_to_cores(alloc.get("cpu", "0"))
        mem_a = mem_to_gib(alloc.get("memory", "0"))
        u = usage.get(n.metadata.name)
        if not u or cpu_a <= 0 or mem_a <= 0:
            continue
        cpu_u = cpu_to_cores(u.get("cpu", "0"))
        mem_u = mem_to_gib(u.get("memory", "0"))
        cpu_p = _pct(cpu_u, cpu_a)
        mem_p = _pct(mem_u, mem_a)
        out.append((n.metadata.name, cpu_p, mem_p, cpu_p >= 80 or mem_p >= 80))
    return out or None


def _node_usage_brief(node_name):
    """该节点 CPU/内存占用一行（如 'CPU 68% | 内存 81%'），拿不到返回 None。"""
    if not node_name:
        return None
    try:
        v1 = _load_v1()
        nodes = {n.metadata.name: n for n in v1.list_node(watch=False).items}
        metrics = _custom_api().list_cluster_custom_object(
            group="metrics.k8s.io", version="v1beta1", plural="nodes"
        )
        usage = {it["metadata"]["name"]: it.get("usage", {}) for it in metrics.get("items", [])}
    except ApiException:
        return None
    node = nodes.get(node_name)
    if not node or node_name not in usage:
        return None
    alloc = node.status.allocatable or {}
    cpu_a = cpu_to_cores(alloc.get("cpu", "0"))
    mem_a = mem_to_gib(alloc.get("memory", "0"))
    u = usage[node_name]
    cpu_u = cpu_to_cores(u.get("cpu", "0"))
    mem_u = mem_to_gib(u.get("memory", "0"))
    if cpu_a <= 0 or mem_a <= 0:
        return None
    return f"CPU {_pct(cpu_u, cpu_a):.0f}% | 内存 {_pct(mem_u, mem_a):.0f}%"


def _inspect_advice(phase, reason, err_lines):
    """规则层归纳常见异常的原因参考与处置建议（只读、参考性）。"""
    r = reason or ""
    reasons, advices = [], []
    if "ImagePullBackOff" in r or "ErrImagePull" in r or "InvalidImageName" in r:
        reasons.append("镜像拉取失败：镜像不存在 / 标签错误 / 仓库无权限 / 节点无法访问镜像仓库")
        advices += ["检查镜像名与标签是否正确（查看该控制器详情）",
                    "确认镜像仓库凭据（imagePullSecrets）已配置且有效",
                    "在节点上手动 docker pull 测试能否拉取"]
    elif "OOMKilled" in r:
        reasons.append("容器内存超过 limit 被内核杀死（OOMKilled）")
        advices += ["调大 resources.limits.memory 后滚动更新",
                    "排查应用内存增长（泄漏 / 大对象），参考下方节点内存占用",
                    "若节点内存接近上限，考虑扩容或迁移负载"]
    elif "CreateContainerConfigError" in r:
        reasons.append("容器配置错误：挂载的 ConfigMap / Secret 缺失，或引用的字段不存在")
        advices += ["检查该控制器挂载的 ConfigMap / Secret 是否存在且包含所需字段",
                    "核对 volumes / volumeMounts 与容器内路径配置"]
    elif "CrashLoopBackOff" in r or "Error" in r or "RunContainerError" in r or "ContainerCannotRun" in r:
        reasons.append("应用反复启动失败（CrashLoopBackOff）：进程启动后异常退出")
        if err_lines:
            reasons.append(f"日志关键线索：{err_lines[0][:120]}")
        advices += ["结合上方日志与事件定位启动失败点（配置、依赖、探针）",
                    "检查环境变量 / ConfigMap 配置是否与当前环境匹配",
                    "若探针失败导致重启，检查 Liveness / Readiness 探针路径与端口"]
    elif phase == "Pending":
        reasons.append("Pod 一直未调度成功（Pending）：资源不足 / 节点污点 / PVC 未就绪 / 镜像拉取中")
        advices += ["查看 Pod 详情的事件（FailedScheduling / Unschedulable）",
                    "检查资源 requests 是否超过节点可分配容量（参考节点资源占用）",
                    "检查 PVC 是否 Bound、节点是否存在不匹配的污点"]
    else:
        reasons.append(f"状态异常：{r or phase}")
        advices.append("先查看 Pod 详情与事件，定位具体原因")
    return reasons, advices


def _pod_list_detail():
    """全部 Pod 明细行（供完整报告落盘）：ns/name [phase] 就绪x/y 重启n次，异常标 ⚠️。"""
    v1 = _load_v1()
    pods = v1.list_pod_for_all_namespaces(watch=False).items
    lines = []
    for p in pods:
        ns = p.metadata.namespace or ""
        name = p.metadata.name or ""
        st = p.status
        phase = (st.phase or "?") if st else "?"
        ready = total = restarts = 0
        cs = (st.container_statuses or []) if st else []
        for c in cs:
            total += 1
            if c.ready:
                ready += 1
            restarts += c.restart_count or 0
        flag = ""
        if phase not in ("Running", "Succeeded", "Completed"):
            flag = " ⚠️"
        else:
            for c in cs:
                w = c.state.waiting if c.state and c.state.waiting else None
                tm = c.state.terminated if c.state and c.state.terminated else None
                if (w and w.reason in _CRASH_REASONS) or (tm and (tm.exit_code or 0) != 0):
                    flag = " ⚠️"
                    break
        lines.append(f"{ns}/{name} [{phase}] {ready}/{total} 重启{restarts}次{flag}")
    lines.sort()
    return lines


def _append_pod_detail(lines):
    """完整报告追加全部 Pod 明细（异常标⚠️）。"""
    lines.append("")
    lines.append("**📋 Pod 明细**")
    try:
        detail = _pod_list_detail()
    except ApiException as exc:
        lines.append(f"（读取 Pod 列表失败: {exc}）")
        return
    lines.append(f"共 {len(detail)} 个：")
    lines.extend("  " + d for d in detail)


def cluster_inspect_report(deep_limit=3, show_limit=8, include_pod_list=False):
    """集群巡检（企微 markdown 美化版）：状态总览 + 节点资源 + 异常分析 + 原因与建议。

    include_pod_list=True 时，在结论前追加全部 Pod 明细（供完整报告落盘；群消息保持精简）。
    """
    total, bad = _abnormal_pods()
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = ["# 🚀 K8s 集群巡检报告", f"> 📅 {now} | 范围: 全部命名空间", ""]

    # ---- 状态总览 ----
    if not bad:
        lines.append("**✅ 状态总览**")
        lines.append(
            f"Pod 总数 <font color=\"info\">{total}</font>"
            f" | 正常 <font color=\"info\">{total}</font>"
            f" | 异常 <font color=\"info\">0</font>"
        )
    else:
        lines.append("**⚠️ 状态总览**")
        lines.append(
            f"Pod 总数 <font color=\"info\">{total}</font>"
            f" | 正常 <font color=\"info\">{total - len(bad)}</font>"
            f" | 异常 <font color=\"warning\">{len(bad)}</font>"
        )

    # ---- 节点资源占用 ----
    node_brief = _node_resources_brief()
    if node_brief:
        lines.append("")
        lines.append("**💻 节点资源占用**")
        for name, cpu_p, mem_p, hot in node_brief:
            color = "warning" if hot else "info"
            lines.append(
                f"- {name}: CPU <font color=\"{color}\">{cpu_p:.0f}%</font>"
                f" | 内存 <font color=\"{color}\">{mem_p:.0f}%</font>"
            )
    lines.append("")

    if not bad:
        if include_pod_list:
            _append_pod_detail(lines)
        lines.append("🏆 **结论**：<font color=\"info\">集群健康，所有 Pod 运行正常 ✅</font>")
        return "\n".join(lines)

    # ---- 异常清单 ----
    bad.sort(key=lambda x: (-x["restart"], x["ns"], x["name"]))
    shown = bad[:show_limit]
    lines.append("**🚨 异常 Pod 清单**")
    for i, b in enumerate(shown, 1):
        node = f" | 节点: {b['node']}" if b["node"] else ""
        lines.append(f"{i}. <font color=\"warning\">{b['ns']}/{b['name']}</font>")
        lines.append(f"   状态: {b['phase']} | 重启 {b['restart']} 次 | 原因: {b['reason']}{node}")
    if len(bad) > show_limit:
        lines.append(f"… 还有 {len(bad) - show_limit} 个未展开（本次仅深度分析前 {deep_limit} 个）")
    lines.append("")

    # ---- 深度分析 ----
    deep = shown[:deep_limit]
    for b in deep:
        ns, name = b["ns"], b["name"]
        lines.append(f"**🔍 {ns}/{name} 深度分析**")
        errs = _pod_error_lines(ns, name)
        if errs is None:
            lines.append("  · 日志: 当前读取失败（容器可能重启中，可稍后查看）")
        elif errs:
            lines.append(f"  · 日志: 最近 100 行内关键错误 {len(errs)} 条")
            for e in errs:
                lines.append(f"    {e[:110]}")
        else:
            lines.append("  · 日志: 最近 100 行未发现错误关键字")
        evs = _recent_events_brief(ns, name)
        if evs:
            lines.append("  · 事件:")
            for e in evs:
                lines.append(f"    {e}")
        ctl, change = _controller_change_brief(ns, b["pod"])
        if ctl:
            lines.append(f"  · 控制器: {ctl}")
            if change:
                lines.append(f"    {change}")
        nb = _node_usage_brief(b["node"])
        if nb:
            lines.append(f"  · 节点 {b['node']}: {nb}")
        lines.append("")

    # ---- 原因与建议 ----
    lines.append("**💡 原因参考与处置建议**（仅参考，需人工确认后操作）")
    for b in deep:
        errs = _pod_error_lines(b["ns"], b["name"])
        reasons, advices = _inspect_advice(b["phase"], b["reason"], errs or [])
        lines.append(f"▶ {b['ns']}/{b['name']}")
        for x in reasons:
            lines.append(f"  · 可能原因: {x}")
        for i, a in enumerate(advices, 1):
            lines.append(f"  {i}. {a}")
        lines.append("")
    if len(bad) > deep_limit:
        lines.append(
            f"💬 还有 {len(bad) - deep_limit} 个异常 Pod 未深度分析，"
            "可发「查看 <pod名> 的详情 / 错误日志」逐个排查"
        )
    if include_pod_list:
        _append_pod_detail(lines)
    lines.append("")
    lines.append(f"🏆 **结论**：<font color=\"warning\">发现 {len(bad)} 个异常 Pod，请尽快关注 ⚠️</font>")
    return "\n".join(lines)


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
            try:
                ns_pods = v1.list_namespaced_pod(namespace, watch=False).items
            except Exception:
                ns_pods = []
            raise K8sQueryError(_pod_not_found_msg(name, namespace, ns_pods))
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


# 错误日志筛选关键词（忽略大小写）
_ERROR_RE = re.compile(
    r"(?:\berr\b|\berror\b|\bexception\b|\bfatal\b|\bcritical\b|\bpanic\b"
    r"|\bfailed\b|\bfailure\b|\btimeout\b|\bdenied\b|\brefused\b"
    r"|错误|异常|失败|出错|报错|超时|拒绝)",
    re.IGNORECASE,
)
# 错误日志最多直接展示条数（超出提示+可下载完整）
_ERR_MAX_SHOW = 30
# 关键词过滤最多直接展示条数（超出提示+可下载完整）
_FILTER_MAX_SHOW = 60


def pod_log_report(pod, lines=None, error_only=False, filter=None):
    if not pod:
        return _LOG_HELP
    found, matches = find_pod(pod)
    if not found:
        names = "\n".join(f"  {ns}/{name}" for ns, name in matches[:10])
        more = f"\n  ...共 {len(matches)} 个，请给出更精确的 Pod 名" if len(matches) > 10 else ""
        return f"匹配到多个 Pod，请指定具体名称：\n{names}{more}"
    ns, name = found
    # 错误筛选模式默认多看一些（200 行），避免错过错误；用户可显式指定行数
    default_lines = 200 if (error_only or filter) else K8S_LOG_DEFAULT_LINES
    effective = max(int(lines or default_lines), 1)
    effective = min(effective, K8S_LOG_MAX_LINES)
    log = get_pod_log(ns, name, effective)
    lines_used = max(1, min(effective, K8S_LOG_MAX_LINES))
    info = f"`{ns}/{name}`"
    raw_body = _ANSI_RE.sub("", log).rstrip()
    if not raw_body.strip():
        return f"**【Pod 日志】** {info}\n（该 Pod 当前无日志输出）"

    tail_note = ""
    save_header = ""
    if error_only:
        err_lines = [ln for ln in raw_body.splitlines() if _ERROR_RE.search(ln)]
        if not err_lines:
            return (f"**🚨【Pod 错误日志】** {info}\n"
                    f"最近 {lines_used} 行中未发现错误（ERROR/Exception/Fatal 等）")
        n_total = len(err_lines)
        show = err_lines[-_ERR_MAX_SHOW:]
        if n_total > len(show):
            tail_note = f"\n（仅显示最近 {len(show)} 条，共 {n_total} 条；可下载完整日志）"
        body = "\n".join(_hl("🚨 " + _clip(ln, 100)) for ln in show)
        header = f"**🚨【Pod 错误日志】** 最近 {lines_used} 行中 {_hl(n_total, 'warning')} 条错误"
        save_header = f"【Pod 错误日志】最近 {lines_used} 行中 {n_total} 条错误"
    elif filter:
        fkw = filter
        fk_low = fkw.lower()  # 大小写不敏感：info/INFO/Info 均可匹配
        matched = [ln for ln in raw_body.splitlines() if fk_low in ln.lower()]
        if not matched:
            return (f"**【Pod 日志筛选】** 包含 “{fkw}”（最近 {lines_used} 行中未找到匹配）\n"
                    f"{info}\n"
                    f"提示：可扩大扫描范围，例如「查看 {name} 的日志 500 行 包含 {fkw}」")
        n_total = len(matched)
        show = matched[-_FILTER_MAX_SHOW:]

        def _hl_kw(ln):
            ln2 = _clip(ln, 100)
            return re.sub(re.escape(fkw), _hl(fkw, "warning"), ln2, flags=re.IGNORECASE)

        body = "\n".join(_hl_kw(ln) for ln in show)
        if n_total > len(show):
            tail_note = f"\n（仅显示最近 {len(show)} 条匹配，共 {n_total} 条；可下载完整日志）"
        header = (f"**【Pod 日志筛选】** 包含 “{_hl(fkw, 'info')}”"
                  f"（最近 {lines_used} 行中 {_hl(n_total, 'info')} 条匹配）")
        save_header = f"【Pod 日志筛选】包含 “{fkw}”（最近 {lines_used} 行中 {n_total} 条匹配）"
    else:
        body = "\n".join(_fmt_log_line(ln) for ln in raw_body.splitlines())
        header = f"**【Pod 日志】** 最近 {lines_used} 行"
        save_header = f"【Pod 日志】最近 {lines_used} 行"

    full = f"{header}\n{info}\n{body}{tail_note}"
    # 落盘版：纯文本完整（不染色、不截断），供下载查看
    uid = _pod_uid(ns, name)
    save_full = (f"{save_header}\n命名空间: {ns} | Pod: {name}"
                 + (f" | UID: {uid}" if uid else "") + f"\n{raw_body}")
    body_lines = raw_body.count("\n") + (0 if raw_body.endswith("\n") else 1)
    if len(full.encode("utf-8")) > MAX_REPLY_BYTES:
        path, fname, n_bytes = _save_log_file(ns, name, save_full)
        if error_only:
            head = f"**🚨【Pod 错误日志】** 共 {n_total} 条错误（{n_bytes} 字节），已保存为日志文件："
        elif filter:
            head = f"**【Pod 日志筛选】** 共 {n_total} 条匹配（{n_bytes} 字节），已保存为日志文件："
        else:
            head = f"**【Pod 日志】** 共 {body_lines} 行（{n_bytes} 字节），超过企微单条消息上限，已保存为日志文件："
        if LOG_DOWNLOAD_BASE_URL:
            url = f"{LOG_DOWNLOAD_BASE_URL}/wecom-k8s-bot-logs/{fname}"
            return (
                f"{head}\n"
                f"[📥 点击下载日志文件]({url})\n"
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

