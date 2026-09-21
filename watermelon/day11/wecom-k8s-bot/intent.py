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

import llm_router

from config import K8S_LOG_DEFAULT_LINES, K8S_LOG_MAX_LINES, LLM_API_KEY

HELP_TEXT = (
    "你好，我是 K8s 查询助手。支持以下查询：\n"
    "1. 查看节点资源占用\n"
    "   例：查看节点资源占用 / top node\n"
    "2. 查看 Pod 日志\n"
    "   例：查看 nginx-abc-123 的日志\n"
    "   例：日志 default/nginx-abc-123 后300行\n"
    "   · 只看错误：查看 nginx-abc-123 的错误日志\n"
    "   · 按关键词筛：查看 nginx-abc-123 的日志 包含 ERROR（大小写不限）\n"
    "   默认最近60行，超长自动保存并提供下载链接\n"
    "3. 查看 Deployment 控制器日志\n"
    "   例：查看 deployment coffee-mp 的日志\n"
    "   例：日志 deploy/coffee-mp 后200行\n"
    "4. 查看 Pod 详情（含退出/重启原因）\n"
    "   例：查看 coffee-mp-xxx 的详情\n"
    "   例：describe po coffee-mp-xxx -n coffee-system\n"
    "5. 查看 Pod 上次退出/重启原因\n"
    "   例：查看 coffee-mp-xxx 的上次退出原因\n"
    "   例：coffee-mp-xxx 为什么重启\n"
    "6. 查看命名空间\n"
    "   例：看看有哪些命名空间\n"
    "7. 查看某命名空间下的 Pod\n"
    "   例：查看 kube-system 的 pod\n"
    "   例：看看 default 命名空间下的 pod\n"
    "8. 排查崩溃/异常 Pod\n"
    "   例：看看哪些 pod 崩溃了\n"
    "   例：coffee-xxx 崩了（自动看它的错误日志）\n"
    "9. 集群巡检\n"
    "   例：巡检 / 体检 / 看看集群有没有问题\n"
    "   自动扫描异常 Pod，分析日志、事件、控制器变更、节点资源，给出原因与建议\n"
    "10. 查看日志/详情用法\n"
    "   例：查看pod日志展示用法 / 查看pod详情展示用法\n"
    "11. 帮助\n"
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


def _extract_filter(text):
    """提取日志过滤关键词：包含/含有/关键字/关键词/grep/过滤/筛选/匹配 后的词。"""
    # 关键词支持类名（java.lang.Exception）与 - / _ 等，取到空白或中文标点为止
    m = re.search(r"grep\s+([^\s，。；]+)", text, re.IGNORECASE)
    if m:
        return m.group(1)
    m = re.search(r"(?:关键字|关键词)[:：为]?\s*([^\s，。；]+)", text)
    if m:
        return m.group(1)
    m = re.search(r"(?:筛选出|包含|含有|过滤|筛选|匹配)[:：]?\s*([^\s，。；]+)", text)
    if m:
        return m.group(1)
    # 日志级别裸词：查看 xxx 的日志 INFO / warn / ERROR / DEBUG 等（大小写不敏感）
    if "日志" in text:
        m = re.search(r"\b(INFO|WARN|WARNING|ERROR|DEBUG|TRACE|FATAL|NOTICE)\b", text, re.IGNORECASE)
        if m:
            return m.group(1).upper()
    return None


def _extract_deployment(text):
    """从文本提取 Deployment 名：deployment coffee-mp / deploy/coffee-mp / deployment 的日志。"""
    m = re.search(r"\bdeploy(?:ment)?\b\s*[:/：]?\s*([A-Za-z0-9][A-Za-z0-9._-]*)", text, re.IGNORECASE)
    if m:
        return m.group(1)
    m = re.search(r"([A-Za-z0-9][A-Za-z0-9._-]*)[^\n]{0,15}?\bdeployment\b[^\n]{0,10}?日志", text, re.IGNORECASE)
    if m:
        return m.group(1)
    return None


# 变更类操作词（重启/删除等，机器人只读，拒绝执行）——强词单独出现即拦截
_CHANGE_STRONG_WORDS = (
    "重启", "restart", "删除", "删掉", "删了", "delete", "停止", "停掉",
    "kill", "扩容", "缩容", "回滚", "驱逐", "evict", "scale", "rollout", "滚动更新",
)
# 弱词需同时出现 k8s 资源词才拦截（避免误伤"创建时间/修改记录"等查询）
_CHANGE_WEAK_WORDS = ("创建", "新建", "修改", "更新", "升级", "edit", "apply")
_CHANGE_RESOURCE_WORDS = (
    "pod", "容器", "deployment", "deploy", "节点", "node", "服务", "service",
    "命名空间", "namespace", "副本", "replica", "job", "cronjob", "ingress",
    "configmap", "secret", "pvc", "pv", "hpa", "statefulset", "daemonset",
)


def _is_change_operation(text):
    """判断是否为"重启/删除/停止…"等非查询操作；返回 True 时应提示无法执行。"""
    low = (text or "").strip().lower()
    if not low:
        return False
    # 日志筛选关键词位置（"包含 X / grep X / 筛选 X"）内的词是关键词，不算变更操作
    kw_span = None
    m = re.search(r"(?:包含|含有|关键字|关键词|grep|筛选|过滤|匹配)\s*[:：]?\s*([^\s，。；]+)", low)
    if m:
        kw_span = (m.start(), m.end())
    for w in _CHANGE_STRONG_WORDS:
        idx = low.find(w)
        if idx == -1:
            continue
        if kw_span and kw_span[0] <= idx < kw_span[1]:
            continue  # 该词是日志筛选关键词，如"包含 deleted"
        # 查询语境中的强词不算变更操作：疑问词/上次 出现在强词前，或"强词+原因/记录/历史"（问原因≠执行操作）
        before = low[max(0, idx - 20):idx]
        if re.search(r"(?:为什么|为何|为啥|怎么|什么原因|上次)", before) or \
           re.search(re.escape(w) + r"(?:的)?\s*(?:原因|记录|历史)", low):
            continue
        return True
    for w in _CHANGE_WEAK_WORDS:
        idx = low.find(w)
        if idx == -1:
            continue
        if kw_span and kw_span[0] <= idx < kw_span[1]:
            continue
        # 排除"创建时间/修改时间/更新时间/记录/历史"等纯查询语境
        if re.search(w + r"\s*(时间|日期|记录|历史)", low):
            continue
        if any(rw in low for rw in _CHANGE_RESOURCE_WORDS):
            return True
    return False


def _extract_pod_and_lines(text):
    """从文本里提取 (pod 名, 日志行数)。pod 名形如 nginx-abc-123 或 ns/nginx-abc-123。"""
    t = text.strip()
    lines = None  # 未指定行数时由查询层按场景决定默认值（普通60 / 错误筛选200）

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


def _extract_pod_last(text):
    """从文本提取 (pod 名, 命名空间)，用于查看上次退出/重启原因。"""
    t = text.strip()
    ns = _extract_namespace(t)
    pod_pattern = r"[A-Za-z0-9][A-Za-z0-9._\-]*(?:/[A-Za-z0-9][A-Za-z0-9._\-]*)?"
    # 形式1: pod名 查看/的/为什么 + 上次退出/退出原因/重启原因（pod 名在前）
    m = re.search(
        r"(" + pod_pattern + r")\s*(?:查看|的|是|为什么|为何|为啥|什么原因)?\s*"
        r"(?:上次退出|退出原因|重启原因|上次重启|退出记录|重启记录|为什么重启|为何重启|为啥重启)",
        t)
    # 形式2: 为什么重启/退出原因 + pod 名（pod 名在后）
    if not m:
        m = re.search(
            r"(?:上次退出|退出原因|重启原因|上次重启|退出记录|重启记录|为什么重启|为何重启|为啥重启)"
            r"\s*(?:的|原因|呢|吗|是)?\s*(" + pod_pattern + r")", t)
    # 形式3: 为什么/为何 + [pod名] + 重启/退出（疑问词在前，中间可隔 pod 名）
    if not m:
        m = re.search(
            r"(?:为什么|为何|为啥|怎么|什么原因)\s*(" + pod_pattern + r"|)"
            r"\s*(?:重启|退出|重启了|退出了)", t)
    # 形式4: 重启/退出的原因是什么 + pod 名
    if not m:
        m = re.search(
            r"(?:重启|退出)(?:的)?原因(?:是|为)?\s*(?:什么)?\s*(" + pod_pattern + r")", t)
    if not m:
        return None, ns
    pod = m.group(1)
    if not pod or pod.lower() in ("pod", "容器", "原因", "退出", "重启", "日志",
                                  "为什么", "为何", "为啥", "怎么"):
        return None, ns
    return pod, ns


def parse_intent(text):
    """返回 (command, params)。"""
    t = strip_mention(text)
    low = t.lower()

    if not t:
        return "help", {}

    if any(k in low for k in ("帮助", "help", "指令", "命令", "功能")):
        return "help", {}

    # 日志/详情用法引导（"查看pod日志展示用法"等）
    if "用法" in low or "怎么用" in low or "展示用法" in low:
        if "日志" in low:
            return "log_help", {}
        if "详情" in low:
            return "detail_help", {}
        return "help", {}

    # 变更类操作（重启/删除/停止等）优先：机器人是只读查询助手，直接提示无法执行
    if _is_change_operation(t):
        return "readonly_denied", {"text": t}

    # 上次退出/重启原因专项查询（先于 LLM 路由，保证稳定命中；"为什么重启"等不再被当变更操作）
    if (any(k in low for k in (
        "上次退出", "退出原因", "重启原因", "上次重启", "为什么重启", "为何重启",
        "为啥重启", "重启的原因", "退出记录", "重启记录",
    )) or ("退出" in low and "原因" in low) or ("重启" in low and "原因" in low)
            or (("为什么" in low or "为何" in low or "为啥" in low or "什么原因" in low)
                and ("重启" in low or "退出" in low))):
        pod, ns = _extract_pod_last(t)
        if pod:
            return "pod_last", {"pod": pod, "namespace": ns}

    # 集群巡检：发现异常 Pod，自动分析日志/事件/控制器变更/节点资源，给原因与建议
    if any(k in low for k in (
        "巡检", "体检", "集群体检", "集群检查", "检查集群", "检查一下集群",
        "集群健康", "集群情况", "集群有没有问题", "集群有没有异常",
        "集群是否正常", "集群是否健康", "inspect",
    )):
        return "cluster_inspect", {}

    # LLM 意图路由：配置了 LLM_API_KEY 则优先用模型解析（更口语化），失败自动降级到下面规则
    if LLM_API_KEY:
        llm_result = llm_router.parse_with_llm(t)
        if llm_result is not None:
            cmd, params = llm_result
            # 兜底：LLM 未识别出日志筛选关键词时（如小写 info 日志），规则层再提取一次
            if cmd == "pod_log" and not params.get("filter"):
                fkw = _extract_filter(t)
                if fkw:
                    params["filter"] = fkw
            # 兜底：deployment 日志的"所有副本"识别
            if cmd == "deploy_log" and not params.get("all_pods"):
                params["all_pods"] = bool(re.search(
                    r"(所有|全部|每个|所有副本|全部副本|多副本|多个副本)", low))
            # 未定义问题：带上原始文本，供人性化回复区分闲聊 / K8s 相关
            if cmd == "unknown":
                params["text"] = t
            return cmd, params

    # 崩溃排查：提到崩溃/挂了/宕机等。有具体 pod 名→自动查错误日志；没有→列出集群异常 Pod
    if any(k in low for k in ("崩溃", "挂了", "挂掉", "崩了", "宕机", "crash")) and "日志" not in low:
        pod, _ = _extract_pod_and_lines(t)
        if not pod:
            # 无"日志"语境时用宽松匹配提取 pod 名（如"coffee-xxx 崩了"）
            m = re.search(r"([A-Za-z0-9][A-Za-z0-9._-]*(?:/[A-Za-z0-9][A-Za-z0-9._-]*)?)", t)
            if m and m.group(1).lower() not in ("pod", "容器", "服务", "哪个", "那个", "这个", "deployment", "deploy"):
                pod = m.group(1)
        if pod:
            return "pod_log", {"pod": pod, "lines": None, "error_only": True, "filter": None}
        return "crash_pods", {}

    if "日志" in low:
        # Deployment 控制器日志优先（形如 deployment coffee-mp 的日志）
        dname = _extract_deployment(t)
        if dname:
            ns = _extract_namespace(t)
            _, lines = _extract_pod_and_lines(t)
            all_pods = bool(re.search(
                r"(所有|全部|每个|所有副本|全部副本|多副本|多个副本)", low))
            return "deploy_log", {"deployment": dname, "namespace": ns, "lines": lines,
                                  "all_pods": all_pods}
        pod, lines = _extract_pod_and_lines(t)
        # 关键词过滤（grep）：明确提到“包含/含有/关键字/grep”等时按关键词筛行，优先于错误筛选
        fkw = _extract_filter(t)
        error_only = (not fkw) and any(k in low for k in (
            "错误", "报错", "异常", "出错", "崩溃", "崩了", "挂了", "宕机",
            "error", "exception", "fatal",
        ))
        if not pod:
            return "log_help", {}
        return "pod_log", {
            "pod": pod, "lines": lines,
            "error_only": error_only or None,
            "filter": fkw,
        }

    # Pod 详情（等价 kubectl describe po；优先于 pod_list/命名空间，避免被抢）
    if "describe" in low:
        pod, ns = _extract_pod_detail(t)
        return "pod_detail", {"pod": pod, "namespace": ns}
    if "详情" in low:
        pod, ns = _extract_pod_detail(t)
        # 能提取到 Pod 名，或明确提到 pod（如“查看 pod 详情”），才走详情；
        # 否则继续往下（避免“查看节点资源详情/命名空间详情”被误伤）
        if pod:
            return "pod_detail", {"pod": pod, "namespace": ns}
        if "pod" in low:
            return "detail_help", {}

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

    return "unknown", {"text": t}
