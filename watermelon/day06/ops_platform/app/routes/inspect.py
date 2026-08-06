import base64
import json
import re
import time
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User, InspectHistory
from app.security import get_current_user
from app.crud import get_assets, get_asset, has_remote_permission, get_user_allowed_asset_ids
from app.services.ssh_client import execute_remote_command
from app.services.crypto import decrypt_password, decrypt_secret
from app.services.audit import write_audit_log, ACTION_EXECUTE, RESOURCE_ASSET

router = APIRouter(prefix="/api/inspect", tags=["集群巡检"])


# K8s 资源名白名单：仅允许小写字母、数字、-、.（DNS 子域规范）
_K8S_NAME_RE = re.compile(r'[^a-z0-9.-]')


def _sanitize_k8s_name(name: str) -> str:
    """白名单方式清洗 K8s 资源名，只保留 [a-z0-9.-] 字符

    用于 plan name / result name / task name 等会拼接到 kubectl 命令的输入，
    避免任何 shell 元字符注入。
    """
    if not name:
        return ""
    # 统一小写后过滤，符合 k8s 命名规范
    return _K8S_NAME_RE.sub('', name.lower())


def _asset_connection_kwargs(db_asset):
    """提取资产 SSH 连接参数并解密密码 / 私钥 / 证书字段"""
    try:
        return {
            "host": db_asset.ip,
            "port": db_asset.port,
            "username": db_asset.username,
            "password": decrypt_password(db_asset.password) if db_asset.password else None,
            "ssh_key": decrypt_secret(db_asset.ssh_key, "SSH私钥") if db_asset.ssh_key else None,
            "ssh_key_path": db_asset.ssh_key_path,
            "ssh_cert": decrypt_secret(db_asset.ssh_cert, "SSH证书") if db_asset.ssh_cert else None,
            "ssh_cert_path": db_asset.ssh_cert_path,
            "sudo_password": decrypt_password(db_asset.sudo_password) if db_asset.sudo_password else None,
            "sudo_enabled": db_asset.sudo_enabled,
            "jump_enabled": db_asset.jump_enabled,
            "jump_host": db_asset.jump_host,
            "jump_port": db_asset.jump_port,
            "jump_username": db_asset.jump_username,
            "jump_password": decrypt_password(db_asset.jump_password) if db_asset.jump_password else None,
            "jump_ssh_key": decrypt_secret(db_asset.jump_ssh_key, "跳板机SSH私钥") if db_asset.jump_ssh_key else None,
            "jump_ssh_key_path": db_asset.jump_ssh_key_path,
            "jump_ssh_cert": decrypt_secret(db_asset.jump_ssh_cert, "跳板机SSH证书") if db_asset.jump_ssh_cert else None,
            "jump_ssh_cert_path": db_asset.jump_ssh_cert_path,
        }
    except ValueError as e:
        raise HTTPException(status_code=500, detail=f"资产敏感字段解密失败，请联系管理员重新配置: {e}")


def _wrap_sudo(conn_kwargs: dict, cmd: str) -> tuple:
    """根据 sudo 配置包装命令，返回 (命令字符串, stdin_data)

    - sudo_enabled=False：原样返回，stdin_data=None
    - sudo_enabled=True + sudo_password：用 sudo -S 通过 stdin 传密码（不进进程列表）
    - sudo_enabled=True + 无 sudo_password：用 sudo -i 无密码提权（NOPASSWD）

    使用 base64 编码命令避免 shell 转义问题。
    sudo -i 加载 root 登录环境（PATH/KUBECONFIG），用 -- 显式分隔 sudo 选项与命令。
    """
    sudo_enabled = conn_kwargs.get("sudo_enabled")
    sudo_pwd = conn_kwargs.get("sudo_password")
    if not sudo_enabled:
        return cmd, None
    cmd_b64 = base64.b64encode(cmd.encode()).decode()
    if sudo_pwd:
        # 密码通过 stdin 写入，不出现在命令行/进程列表中
        wrapped = f"sudo -S -p '' -- bash -c 'echo {cmd_b64} | base64 -d | bash' 2>&1"
        return wrapped, sudo_pwd
    else:
        # 无密码 sudo：用 sudo -i 加载 root 登录环境后执行
        # -- 显式分隔 sudo 选项和命令，避免参数解析歧义
        return f"sudo -i -- bash -c 'echo {cmd_b64} | base64 -d | bash' 2>&1", None


def _exec(conn_kwargs: dict, cmd: str, command_timeout: int = 30) -> tuple:
    """执行远程命令的便捷封装：自动应用 sudo 包装

    返回 (success, result_dict)
    """
    wrapped_cmd, stdin_data = _wrap_sudo(conn_kwargs, cmd)
    return execute_remote_command(
        **conn_kwargs,
        command=wrapped_cmd,
        command_timeout=command_timeout,
        stdin_data=stdin_data,
    )


# KubeEye 巡检命令：获取 CRD 数据 + kubeeye-apiserver API 结果
_INSPECT_CMD = (
    'CRDS=$(kubectl get crd -o custom-columns=NAME:.metadata.name --no-headers 2>/dev/null | grep -i eye); '
    'if [ -z "$CRDS" ]; then '
    '  echo "NO_KUBEEYE_CRD"; '
    'else '
    '  echo "===CRDS_FOUND==="; echo "$CRDS"; '
    '  echo "===CRD:inspectresult==="; kubectl get inspectresult -A -o json 2>/dev/null || echo "{}"; '
    '  echo "===CRD:inspectplan==="; kubectl get inspectplan -A -o json 2>/dev/null || echo "{}"; '
    '  echo "===CRD:inspectrule==="; kubectl get inspectrule -A -o json 2>/dev/null || echo "{}"; '
    '  echo "===KUBEEYE_APISERVER==="; '
    '  SVC_IP=$(kubectl get svc -n kubeeye-system kubeeye-apiserver '
    '-o custom-columns=CLUSTER-IP:.spec.clusterIP --no-headers 2>/dev/null | head -1); '
    '  SVC_PORT=$(kubectl get svc -n kubeeye-system kubeeye-apiserver '
    '-o jsonpath="{.spec.ports[0].port}" 2>/dev/null); '
    '  if [ -n "$SVC_IP" ]; then '
    '    for NAME in $(kubectl get inspectresult -A -o jsonpath="{.items[*].metadata.name}" 2>/dev/null); do '
    '      echo "===API_RESULT:${NAME}==="; '
    '      curl -s --max-time 10 -H "Accept: application/json" '
    '"http://${SVC_IP}:${SVC_PORT:-9090}'
    '/kapis/kubeeye.kubesphere.io/v1alpha2/inspectresults/${NAME}" 2>/dev/null || echo "{}"; '
    '    done; '
    '  else '
    '    echo "APISERVER_NOT_FOUND"; '
    '  fi; '
    'fi'
)


class InspectRequest(BaseModel):
    asset_id: int
    task_name: str = ""


class ReportRequest(BaseModel):
    asset_id: int
    result_name: str


@router.get("/assets")
def list_inspect_assets(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """列出可用于巡检的资产（优先展示 k8s/kube/master 角色的资产）"""
    # 防越权：非管理员仅能查看自己有权限的资产
    allowed_ids = get_user_allowed_asset_ids(db, current_user)
    assets = get_assets(db, limit=500, allowed_asset_ids=allowed_ids)
    k8s_assets = []
    other_assets = []
    for a in assets:
        item = {
            "id": a.id,
            "hostname": a.hostname,
            "ip": a.ip,
            "env": a.env,
            "role": a.role,
        }
        if a.role and re.search(r"k8s|kube|master", a.role, re.IGNORECASE):
            k8s_assets.append(item)
        else:
            other_assets.append(item)
    return {"k8s_assets": k8s_assets, "other_assets": other_assets}


@router.post("/run")
def run_inspect(
    request: InspectRequest,
    req: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """触发新的 KubeEye 巡检任务，立即返回任务信息（异步模式）"""
    asset_id = request.asset_id
    task_name = request.task_name or ""
    db_asset = get_asset(db, asset_id)
    if not db_asset:
        raise HTTPException(status_code=404, detail="资产不存在")

    if not has_remote_permission(db, current_user.id, asset_id, "connect"):
        raise HTTPException(status_code=403, detail="没有该服务器的远程操作权限")

    if not db_asset.password and not db_asset.ssh_key and not db_asset.ssh_key_path:
        raise HTTPException(status_code=400, detail="该资产未配置认证方式，无法连接")

    conn_kwargs = _asset_connection_kwargs(db_asset)

    # 检测 KubeEye 环境 + 获取 inspectplan
    # 不吞 stderr：kubectl 失败时能看到真实原因（权限/PATH/连接问题）
    _init_cmd = (
        'echo "===DEBUG_USER===" && whoami && '
        'echo "===DEBUG_PATH===" && echo "$PATH" && '
        'echo "===DEBUG_KUBECTL===" && (kubectl version --client 2>&1 | head -1) && '
        'CRDS=$(kubectl get crd -o custom-columns=NAME:.metadata.name --no-headers 2>&1 | grep -i eye); '
        'if [ -z "$CRDS" ]; then '
        '  echo "NO_KUBEEYE_CRD"; '
        '  echo "===DEBUG_CRD_RAW==="; kubectl get crd 2>&1 | head -5; '
        'else '
        '  echo "===CRDS_FOUND==="; echo "$CRDS"; '
        '  echo "===CRD:inspectplan==="; kubectl get inspectplan -A -o json 2>/dev/null || echo "{}"; '
        '  echo "===CRD:inspectresult==="; kubectl get inspectresult -A -o json 2>/dev/null || echo "{}"; '
        'fi'
    )
    success0, result0 = _exec(conn_kwargs, _init_cmd, command_timeout=30)
    if not success0:
        raise HTTPException(
            status_code=500,
            detail=f"SSH 连接失败: {result0.get('error', '未知错误')}",
        )
    init_stdout = result0.get("stdout", "")

    if "NO_KUBEEYE_CRD" in init_stdout:
        # 提取调试信息，帮助排查 sudo/PATH/kubectl 权限问题
        debug_info = ""
        for marker in ("===DEBUG_USER===", "===DEBUG_PATH===", "===DEBUG_KUBECTL===", "===DEBUG_CRD_RAW==="):
            if marker in init_stdout:
                # 截取该标记后到下一个 === 标记或行尾的内容
                start = init_stdout.index(marker) + len(marker)
                rest = init_stdout[start:]
                # 找下一个 === 标记或换行
                next_mark = re.search(r'===\w+===|\n', rest)
                end = next_mark.start() if next_mark else len(rest)
                val = rest[:end].strip()
                label = marker.replace("===", "").replace("DEBUG_", "")
                debug_info += f"{label}: {val}\n"
            else:
                label = marker.replace("===", "").replace("DEBUG_", "")
                debug_info += f"{label}: (未输出)\n"

        sudo_status = "已启用" if conn_kwargs.get("sudo_enabled") else "未启用"
        init_stderr = result0.get("stderr", "")
        msg = "未在集群中发现 KubeEye 相关 CRD。请确认 KubeEye Operator 已正确安装。"
        msg += f"\n\n[调试信息] sudo: {sudo_status}\n{debug_info}"
        if init_stderr:
            msg += f"[stderr]\n{init_stderr[:500]}\n"
        if not init_stdout:
            msg += f"\n[警告] stdout 完全为空，可能 SSH 执行失败或命令被卡住"
            msg += f"\n[result0] success={success0}, keys={list(result0.keys())}"

        write_audit_log(
            db, username=current_user.username, action=ACTION_EXECUTE,
            resource_type=RESOURCE_ASSET, resource_name=db_asset.hostname,
            detail=f"集群巡检[{task_name}]: 未发现 KubeEye CRD" if task_name else "集群巡检: 未发现 KubeEye CRD",
            ip_address=req.client.host if req.client else None,
        )
        return {
            "status": "no_kubeeye",
            "asset": {"id": db_asset.id, "hostname": db_asset.hostname, "ip": db_asset.ip},
            "task_name": task_name,
            "message": msg,
            "debug_info": debug_info,
            "sudo_enabled": conn_kwargs.get("sudo_enabled"),
            "raw_stdout": init_stdout[:500],
            "raw_stderr": init_stderr[:500],
        }

    # 解析已有 plan 和 result
    current_plan = None
    existing_result_names = set()
    blocks = re.split(r"===CRD:([^=]+)===", init_stdout)
    for idx in range(1, len(blocks) - 1, 2):
        crd_n = blocks[idx].strip()
        js = blocks[idx + 1].strip()
        if not js or js == "{}":
            continue
        try:
            data = json.loads(js)
        except json.JSONDecodeError:
            continue
        if crd_n.startswith("inspectplan"):
            for it in data.get("items", []):
                current_plan = it.get("metadata", {}).get("name", "")
                break
        elif crd_n.startswith("inspectresult"):
            for it in data.get("items", []):
                existing_result_names.add(it.get("metadata", {}).get("name", ""))

    if not current_plan:
        return {
            "status": "no_plan",
            "asset": {"id": db_asset.id, "hostname": db_asset.hostname, "ip": db_asset.ip},
            "task_name": task_name,
            "message": "集群中未找到 InspectPlan，请先创建巡检计划。",
        }

    # 创建新的 InspectTask：从 InspectPlan 复制 spec，只改 kind 和 name
    ts = time.strftime("%Y%m%d-%H-%M")
    # 先清洗 plan 名，再拼时间戳，避免 shell 元字符注入
    safe_plan = _sanitize_k8s_name(current_plan)
    new_task_name = f"{safe_plan}-{ts}"

    # 第 1 步：获取 InspectPlan 的 JSON
    get_plan_cmd = f'kubectl get inspectplan {safe_plan} -o json 2>/dev/null'
    sp, rp = _exec(conn_kwargs, get_plan_cmd, command_timeout=15)

    if not sp:
        return {
            "status": "create_failed",
            "asset": {"id": db_asset.id, "hostname": db_asset.hostname, "ip": db_asset.ip},
            "task_name": task_name,
            "kubeeye_task": new_task_name,
            "message": f"获取 InspectPlan 失败: {rp.get('error', '未知错误')}",
        }

    plan_json_str = rp.get("stdout", "").strip()
    try:
        plan_data = json.loads(plan_json_str)
    except json.JSONDecodeError:
        return {
            "status": "create_failed",
            "asset": {"id": db_asset.id, "hostname": db_asset.hostname, "ip": db_asset.ip},
            "task_name": task_name,
            "kubeeye_task": new_task_name,
            "message": f"解析 InspectPlan JSON 失败",
        }

    # 第 2 步：在本地 Python 中变换为 InspectTask
    plan_data["kind"] = "InspectTask"
    plan_data["metadata"]["name"] = new_task_name
    # 添加 label 关联 plan
    labels = plan_data["metadata"].setdefault("labels", {})
    labels["kubeeye.kubesphere.io/plan-name"] = safe_plan
    # 删除 k8s 自动管理的 metadata 字段
    for k in ("resourceVersion", "uid", "creationTimestamp", "generation",
              "managedFields", "ownerReferences", "finalizers"):
        plan_data["metadata"].pop(k, None)
    # 删除 status
    plan_data.pop("status", None)
    # 删除不需要的 annotations
    plan_data["metadata"].pop("annotations", None)
    # 删除 InspectPlan 专有的 spec 字段（InspectTask 不支持这些）
    spec = plan_data.get("spec", {})
    for k in ("maxTasks", "suspend", "schedule"):
        spec.pop(k, None)

    task_json = json.dumps(plan_data, ensure_ascii=False)
    task_b64 = base64.b64encode(task_json.encode()).decode()

    # 第 3 步：通过 base64 apply
    create_cmd = f'echo "{task_b64}" | base64 -d | kubectl apply -f - 2>&1'

    succ1, res1 = _exec(conn_kwargs, create_cmd, command_timeout=30)
    create_output = ""
    if succ1:
        create_output = res1.get("stdout", "") + res1.get("stderr", "")
    else:
        create_output = res1.get("error", "未知错误")

    # 检查是否真正创建成功（kubectl apply 输出包含 "created" 或 "configured"）
    created_ok = succ1 and ("created" in create_output.lower() or "configured" in create_output.lower())

    if not created_ok:
        # 验证任务是否实际已创建
        verify_cmd = f'kubectl get inspecttask {new_task_name} -o name 2>/dev/null || echo "NOT_FOUND"'
        sv, sr = _exec(conn_kwargs, verify_cmd, command_timeout=15)
        if sv and new_task_name in sr.get("stdout", ""):
            create_output += f"  (任务实际已创建)"
        else:
            return {
                "status": "create_failed",
                "asset": {"id": db_asset.id, "hostname": db_asset.hostname, "ip": db_asset.ip},
                "task_name": task_name,
                "kubeeye_task": new_task_name,
                "message": f"创建巡检任务失败: {create_output[:500]}",
            }

    write_audit_log(
        db, username=current_user.username, action=ACTION_EXECUTE,
        resource_type=RESOURCE_ASSET, resource_name=db_asset.hostname,
        detail=f"集群巡检[{task_name}]: 触发新任务 {new_task_name}",
        ip_address=req.client.host if req.client else None,
    )

    return {
        "status": "triggered",
        "asset": {"id": db_asset.id, "hostname": db_asset.hostname, "ip": db_asset.ip},
        "task_name": task_name,
        "kubeeye_task": new_task_name,
        "existing_results": list(existing_result_names),
        "create_output": create_output[:500],
        "message": f"巡检任务 {new_task_name} 已触发，正在等待 KubeEye 执行完成...",
    }


class PollRequest(BaseModel):
    asset_id: int
    kubeeye_task: str = ""
    task_name: str = ""
    existing_results: str = ""


@router.post("/poll")
def poll_inspect(
    request: PollRequest,
    req: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """轮询巡检任务状态，完成后返回完整结果"""
    db_asset = get_asset(db, request.asset_id)
    if not db_asset:
        raise HTTPException(status_code=404, detail="资产不存在")

    if not has_remote_permission(db, current_user.id, request.asset_id, "connect"):
        raise HTTPException(status_code=403, detail="没有该服务器的远程操作权限")

    conn_kwargs = _asset_connection_kwargs(db_asset)

    # 查询当前 inspecttask 和 inspectresult 状态
    check_cmd = (
        'echo "===CRD:inspecttask==="; kubectl get inspecttask -A -o json 2>/dev/null || echo "{}"; '
        'echo "===CRD:inspectresult==="; kubectl get inspectresult -A -o json 2>/dev/null || echo "{}"'
    )
    succ, res = _exec(conn_kwargs, check_cmd, command_timeout=20)
    if not succ:
        return {"status": "error", "message": f"SSH 连接失败: {res.get('error', '')}"}

    stdout = res.get("stdout", "")
    existing_set = set(request.existing_results.split(",")) if request.existing_results else set()

    # 从 task name 提取时间戳用于匹配 result 名（如 task: inspectplan-20260804-09-52 → result: *-20260804-09-52-result）
    ts_match = None
    task_parts = request.kubeeye_task.rsplit("-", 2)
    if len(task_parts) >= 2:
        ts_match = "-".join(task_parts[-2:])  # e.g., "20260804-09-52"

    # 检查新 task 是否完成 + 查找新 result
    task_complete = False
    new_result_name = None
    all_task_items = []
    all_result_items = []

    blocks = re.split(r"===CRD:([^=]+)===", stdout)
    for idx in range(1, len(blocks) - 1, 2):
        crd_n = blocks[idx].strip()
        js = blocks[idx + 1].strip()
        if not js or js == "{}":
            continue
        try:
            data = json.loads(js)
        except json.JSONDecodeError:
            continue

        if crd_n.startswith("inspecttask"):
            for it in data.get("items", []):
                tname = it.get("metadata", {}).get("name", "")
                all_task_items.append(tname)
                if tname == request.kubeeye_task:
                    status = it.get("status", {})
                    task_complete = status.get("complete", False) or status.get("phase", "") == "Completed"

        if crd_n.startswith("inspectresult"):
            for it in data.get("items", []):
                rname = it.get("metadata", {}).get("name", "")
                rcomplete = it.get("status", {}).get("complete", False)
                all_result_items.append(rname)
                # 优先通过时间戳匹配（最可靠），其次看是否在已有列表之外
                if rname and ts_match and ts_match in rname:
                    if rcomplete:
                        new_result_name = rname
                elif rname and rname not in existing_set and rcomplete:
                    new_result_name = rname

    # 如果按时间戳匹配到了 result，或 task 已 complete 且有新 result
    if not new_result_name and task_complete:
        # 任务完成但 result 还没出现（慢一步），再看一次所有 result
        for rname in all_result_items:
            if rname not in existing_set:
                new_result_name = rname
                break

    # 如果新 result 已出现，获取完整数据
    if new_result_name:
        # 执行完整数据采集
        succ2, res2 = _exec(conn_kwargs, _INSPECT_CMD, command_timeout=120)
        if not succ2:
            return {"status": "error", "message": "获取巡检结果失败"}

        full_stdout = res2.get("stdout", "")
        parsed = _parse_kubeeye_results(full_stdout)

        # 只展示新结果
        parsed["inspect_tasks"] = [t for t in parsed.get("inspect_tasks", []) if t.get("name") == new_result_name]
        parsed["results"] = [r for r in parsed.get("results", []) if r.get("task") == new_result_name]

        # 如果 JSON 解析为 0，尝试 HTML
        html_debug = {"enrich_attempted": False, "html_fetched": False, "html_length": 0, "findings_parsed": 0, "html_snippet": ""}
        if parsed["summary"]["total"] == 0 and parsed["inspect_tasks"]:
            html_debug["enrich_attempted"] = True
            _enrich_results_from_html(
                parsed, conn_kwargs,
                result_names=[t["name"] for t in parsed["inspect_tasks"]],
                command_timeout=60,
                debug_info=html_debug,
            )

        # 重新统计
        total_danger = sum(1 for r in parsed["results"] if r.get("level") == "error")
        total_warning = sum(1 for r in parsed["results"] if r.get("level") == "warning")
        total_info = sum(1 for r in parsed["results"] if r.get("level") == "info")
        parsed["summary"] = {
            "total": total_danger + total_warning,
            "error": total_danger,
            "warning": total_warning,
            "info": total_info,
        }

        # 保存历史记录
        for t in parsed["inspect_tasks"]:
            level_stats = t.get("level_stats", {})
            history = InspectHistory(
                task_name=request.task_name,
                asset_id=db_asset.id,
                asset_hostname=db_asset.hostname,
                asset_ip=db_asset.ip,
                result_name=t.get("name", ""),
                danger_count=level_stats.get("danger", 0),
                warning_count=level_stats.get("warning", 0),
                ignore_count=level_stats.get("ignore", 0),
                rule_total=json.dumps(t.get("rule_total", {}), ensure_ascii=False),
                start_time=t.get("start_time", ""),
                end_time=t.get("end_time", ""),
                operator=current_user.username,
                raw_summary=json.dumps(parsed["summary"], ensure_ascii=False),
            )
            db.add(history)
        db.commit()

        write_audit_log(
            db, username=current_user.username, action=ACTION_EXECUTE,
            resource_type=RESOURCE_ASSET, resource_name=db_asset.hostname,
            detail=f"集群巡检[{request.task_name}]: 完成，{parsed['summary']['total']} 条问题",
            ip_address=req.client.host if req.client else None,
        )

        return {
            "status": "completed",
            "asset": {"id": db_asset.id, "hostname": db_asset.hostname, "ip": db_asset.ip},
            "task_name": request.task_name,
            "crds_found": parsed["crds_found"],
            "results": parsed["results"],
            "inspect_tasks": parsed.get("inspect_tasks", []),
            "summary": parsed["summary"],
            "debug": html_debug,
        }

    # 任务还未完成
    return {
        "status": "running" if task_complete is False else "processing",
        "task_complete": task_complete,
        "message": f"巡检任务 {request.kubeeye_task} 正在执行中..." if not task_complete
                    else f"巡检任务已完成，正在生成报告...",
        "debug": {
            "task_found": request.kubeeye_task in all_task_items,
            "task_complete": task_complete,
            "total_tasks": len(all_task_items),
            "total_results": len(all_result_items),
            "existing_results_count": len(existing_set),
            "ts_match": ts_match,
        }
    }


class CancelRequest(BaseModel):
    asset_id: int
    kubeeye_task: str
    task_name: str = ""


@router.post("/cancel")
def cancel_inspect(
    request: CancelRequest,
    req: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """取消正在执行的巡检任务：删除远程 KubeEye InspectTask

    前端停止轮询由前端自行处理，此接口只负责清理远端资源。
    """
    db_asset = get_asset(db, request.asset_id)
    if not db_asset:
        raise HTTPException(status_code=404, detail="资产不存在")

    if not has_remote_permission(db, current_user.id, request.asset_id, "connect"):
        raise HTTPException(status_code=403, detail="没有该服务器的远程操作权限")

    kubeeye_task = request.kubeeye_task.strip()
    if not kubeeye_task:
        raise HTTPException(status_code=400, detail="缺少巡检任务名")

    # 清洗任务名，防止 shell 注入（虽然触发时已清洗，这里防御性再清洗一次）
    safe_task = _sanitize_k8s_name(kubeeye_task)
    if not safe_task:
        raise HTTPException(status_code=400, detail="巡检任务名无效")

    conn_kwargs = _asset_connection_kwargs(db_asset)

    # 删除 InspectTask：
    # --ignore-not-found=true：任务可能已自动清理，不报错
    # --wait=false：不等待 finalizer/grace period，立即返回（否则默认等 30 秒）
    # --force --grace-period=0：强制立即删除，不等待优雅终止
    del_cmd = f'kubectl delete inspecttask {safe_task} --ignore-not-found=true --wait=false --force --grace-period=0 2>&1'
    succ, res = _exec(conn_kwargs, del_cmd, command_timeout=15)

    output = ""
    if succ:
        output = (res.get("stdout", "") + res.get("stderr", "")).strip()
    else:
        output = res.get("error", "未知错误")

    # kubectl delete 成功输出 "deleted"，已不存在输出 "NotFound"
    # 即使 SSH 报错，也写审计日志记录取消尝试
    write_audit_log(
        db, username=current_user.username, action=ACTION_EXECUTE,
        resource_type=RESOURCE_ASSET, resource_name=db_asset.hostname,
        detail=f"集群巡检[{request.task_name}]: 取消任务 {safe_task}",
        ip_address=req.client.host if req.client else None,
    )

    cancelled_ok = succ and ("deleted" in output.lower() or "notfound" in output.lower().replace(" ", ""))
    if not cancelled_ok:
        return {
            "success": False,
            "message": f"取消巡检任务失败: {output[:300]}",
        }

    return {
        "success": True,
        "message": f"巡检任务 {safe_task} 已取消",
    }


@router.get("/history")
def list_inspect_history(
    asset_id: int = 0,
    limit: int = 50,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取巡检历史记录列表"""
    query = db.query(InspectHistory)
    if asset_id:
        query = query.filter(InspectHistory.asset_id == asset_id)
    # 防越权：非管理员仅能查看自己有权限资产的巡检历史
    if current_user.role != "admin":
        allowed_ids = get_user_allowed_asset_ids(db, current_user) or set()
        if not allowed_ids:
            return []
        query = query.filter(InspectHistory.asset_id.in_(allowed_ids))
    records = query.order_by(InspectHistory.id.desc()).limit(limit).all()
    return [
        {
            "id": r.id,
            "task_name": r.task_name or "",
            "asset_id": r.asset_id,
            "asset_hostname": r.asset_hostname,
            "asset_ip": r.asset_ip,
            "result_name": r.result_name or "",
            "danger_count": r.danger_count,
            "warning_count": r.warning_count,
            "ignore_count": r.ignore_count,
            "rule_total": r.rule_total or "",
            "start_time": r.start_time or "",
            "end_time": r.end_time or "",
            "operator": r.operator or "",
            "created_at": r.created_at.strftime("%Y-%m-%d %H:%M:%S") if r.created_at else "",
        }
        for r in records
    ]


@router.delete("/history/{history_id}")
def delete_inspect_history(
    history_id: int,
    req: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """删除巡检历史记录（仅删除 Web 页面本地记录，不删除 KubeEye 集群资源）"""
    record = db.query(InspectHistory).filter(InspectHistory.id == history_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="巡检记录不存在")
    # 防越权：非管理员只能删除自己有权限资产的巡检历史
    if current_user.role != "admin" and not has_remote_permission(db, current_user.id, record.asset_id, "connect"):
        # 与查询接口一致，无权限也返回 404，避免暴露记录存在性
        raise HTTPException(status_code=404, detail="巡检记录不存在")

    # 先保留要写入审计日志的信息，再删除
    info_task = record.task_name or record.asset_hostname
    info_result = record.result_name or record.id
    info_host = req.client.host if req.client else None

    try:
        db.delete(record)
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"删除失败: {e}")

    # 审计日志单独 try，失败不影响删除结果（已提交）
    try:
        write_audit_log(
            db, username=current_user.username, action=ACTION_DELETE,
            resource_type=RESOURCE_ASSET, resource_name=info_task,
            detail=f"删除巡检历史记录（仅Web）: {info_result}",
            ip_address=info_host,
        )
    except Exception:
        pass

    return {"detail": "已删除"}


class DeleteInspectTaskRequest(BaseModel):
    asset_id: int
    result_name: str
    task_name: str = ""
    remote_delete: bool = False  # 是否同时删除 KubeEye 集群中的资源，默认 False


@router.post("/task/delete")
def delete_inspect_task(
    request: DeleteInspectTaskRequest,
    req: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """删除巡检任务（默认仅删除 Web 页面本地记录，remote_delete=True 时同时删除 KubeEye 集群资源）"""
    db_asset = get_asset(db, request.asset_id)
    if not db_asset:
        raise HTTPException(status_code=404, detail="资产不存在")

    if not has_remote_permission(db, current_user.id, request.asset_id, "connect"):
        raise HTTPException(status_code=403, detail="没有该服务器的远程操作权限")

    errors = []

    # 如果指定了同时删除集群资源
    if request.remote_delete:
        conn_kwargs = _asset_connection_kwargs(db_asset)
        safe_result = _sanitize_k8s_name(request.result_name)
        plan_name = safe_result.rsplit("-result", 1)[0] if safe_result.endswith("-result") else safe_result

        # 删除 InspectResult
        succ_r, res_r = _exec(conn_kwargs, f'kubectl delete inspectresult {safe_result} 2>&1', command_timeout=30)
        result_msg = res_r.get("stdout", "") + res_r.get("stderr", "") if succ_r else res_r.get("error", "")
        if not succ_r or "error" in result_msg.lower():
            errors.append(f"InspectResult: {result_msg[:200]}")

        # 删除 InspectTask
        safe_task = request.task_name or plan_name
        if safe_task:
            safe_task = _sanitize_k8s_name(safe_task)
            succ_t, res_t = _exec(conn_kwargs, f'kubectl delete inspecttask {safe_task} 2>&1', command_timeout=30)
            task_msg = res_t.get("stdout", "") + res_t.get("stderr", "") if succ_t else res_t.get("error", "")
            if not succ_t or "error" in task_msg.lower():
                errors.append(f"InspectTask: {task_msg[:200]}")

    # 删除本地历史记录
    history = db.query(InspectHistory).filter(
        InspectHistory.asset_id == request.asset_id,
        InspectHistory.result_name == request.result_name,
    ).first()
    if history:
        try:
            db.delete(history)
            db.commit()
        except Exception as e:
            db.rollback()
            raise HTTPException(status_code=500, detail=f"删除本地记录失败: {e}")

    # 审计日志单独 try，失败不影响删除结果
    try:
        write_audit_log(
            db, username=current_user.username, action=ACTION_DELETE,
            resource_type=RESOURCE_ASSET, resource_name=db_asset.hostname,
            detail=f"删除巡检历史记录（仅Web）: {request.result_name}",
            ip_address=req.client.host if req.client else None,
        )
    except Exception:
        pass

    return {"detail": "已删除", "result_name": request.result_name, "warnings": errors if errors else []}


@router.post("/report/download")
def download_inspect_report(
    request: ReportRequest,
    req: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """下载巡检 HTML 报告为文件"""
    asset_id = request.asset_id
    result_name = request.result_name
    db_asset = get_asset(db, asset_id)
    if not db_asset:
        raise HTTPException(status_code=404, detail="资产不存在")

    if not has_remote_permission(db, current_user.id, asset_id, "connect"):
        raise HTTPException(status_code=403, detail="没有该服务器的远程操作权限")

    conn_kwargs = _asset_connection_kwargs(db_asset)
    safe_name = _sanitize_k8s_name(result_name)

    report_cmd = (
        f'SVC_IP=$(kubectl get svc -n kubeeye-system kubeeye-apiserver '
        f'-o custom-columns=CLUSTER-IP:.spec.clusterIP --no-headers 2>/dev/null); '
        f'SVC_PORT=$(kubectl get svc -n kubeeye-system kubeeye-apiserver '
        f'-o jsonpath="{{.spec.ports[0].port}}" 2>/dev/null); '
        f'curl -s --max-time 15 "http://${{SVC_IP}}:${{SVC_PORT:-9090}}'
        f'/kapis/kubeeye.kubesphere.io/v1alpha2/inspectresults/{safe_name}?type=html"'
    )

    success, result = _exec(conn_kwargs, report_cmd, command_timeout=30)

    if not success:
        raise HTTPException(status_code=500, detail=f"SSH 连接失败: {result.get('error', '未知错误')}")

    html = result.get("stdout", "")
    if not html or ("<html" not in html.lower() and "<!DOCTYPE" not in html.upper()):
        raise HTTPException(
            status_code=500,
            detail="获取报告失败，kubeeye-apiserver 可能不可用或报告不存在",
        )

    write_audit_log(
        db, username=current_user.username, action=ACTION_EXECUTE,
        resource_type=RESOURCE_ASSET, resource_name=db_asset.hostname,
        detail=f"下载巡检报告: {result_name}",
        ip_address=req.client.host if req.client else None,
    )

    filename = f"inspect-{result_name}.html"
    return Response(
        content=html,
        media_type="text/html",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )


@router.post("/report")
def get_inspect_report(
    request: ReportRequest,
    req: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """下载指定巡检结果的 HTML 报告

    通过 SSH 在 master 节点上 curl kubeeye-apiserver 获取 HTML 报告。
    """
    asset_id = request.asset_id
    result_name = request.result_name
    db_asset = get_asset(db, asset_id)
    if not db_asset:
        raise HTTPException(status_code=404, detail="资产不存在")

    if not has_remote_permission(db, current_user.id, asset_id, "connect"):
        raise HTTPException(status_code=403, detail="没有该服务器的远程操作权限")

    # 转义 result_name 防止命令注入
    conn_kwargs = _asset_connection_kwargs(db_asset)
    safe_name = _sanitize_k8s_name(result_name)

    report_cmd = (
        f'SVC_IP=$(kubectl get svc -n kubeeye-system kubeeye-apiserver '
        f'-o custom-columns=CLUSTER-IP:.spec.clusterIP --no-headers 2>/dev/null); '
        f'SVC_PORT=$(kubectl get svc -n kubeeye-system kubeeye-apiserver '
        f'-o jsonpath="{{.spec.ports[0].port}}" 2>/dev/null); '
        f'curl -s --max-time 15 "http://${{SVC_IP}}:${{SVC_PORT:-9090}}'
        f'/kapis/kubeeye.kubesphere.io/v1alpha2/inspectresults/{safe_name}?type=html"'
    )

    success, result = _exec(conn_kwargs, report_cmd, command_timeout=30)

    if not success:
        raise HTTPException(status_code=500, detail=f"SSH 连接失败: {result.get('error', '未知错误')}")

    html = result.get("stdout", "")
    if not html or "<html" not in html.lower() and "<!DOCTYPE" not in html.upper():
        raise HTTPException(
            status_code=500,
            detail="获取报告失败，kubeeye-apiserver 可能不可用或报告不存在",
        )

    # 解析 HTML 报告中的告警数据
    findings = _parse_html_report_table(html, result_name)
    total_danger = sum(1 for f in findings if f.get("level") == "error")
    total_warning = sum(1 for f in findings if f.get("level") == "warning")
    total_info = sum(1 for f in findings if f.get("level") == "info")

    write_audit_log(
        db, username=current_user.username, action=ACTION_EXECUTE,
        resource_type=RESOURCE_ASSET, resource_name=db_asset.hostname,
        detail=f"查看巡检报告: {result_name}（{total_danger + total_warning} 条告警）",
        ip_address=req.client.host if req.client else None,
    )

    return {
        "html": html,
        "result_name": result_name,
        "findings": findings,
        "summary": {
            "total": total_danger + total_warning,
            "error": total_danger,
            "warning": total_warning,
            "info": total_info,
        },
    }


def _parse_kubeeye_results(stdout: str) -> dict:
    """解析 KubeEye v1.0 kubectl 输出 + kubeeye-apiserver API 结果

    KubeEye v1.0 的 InspectResult CRD 中 opaResult 为空，
    实际告警数据需要从 kubeeye-apiserver API 获取。
    API 返回完整的 InspectResult 对象，包含正确的 status.level 和填充的 opaResult。
    """
    crds_found = []
    results = []
    inspect_tasks = []
    plan_status = []
    api_result_map = {}

    # 提取 CRD 列表
    crds_match = re.search(r"===CRDS_FOUND===\n(.*?)(?====CRD:|$)", stdout, re.DOTALL)
    if crds_match:
        crds_found = [c.strip() for c in crds_match.group(1).strip().split("\n") if c.strip()]

    # 解析 API 结果块
    api_blocks = re.split(r"===API_RESULT:([^=]+)===", stdout)
    i = 1
    while i < len(api_blocks) - 1:
        result_name = api_blocks[i].strip()
        json_str = api_blocks[i + 1].strip()
        if json_str and json_str != "{}" and json_str != "APISERVER_NOT_FOUND":
            try:
                api_result = json.loads(json_str)
                api_result_map[result_name] = api_result
            except json.JSONDecodeError:
                pass
        i += 2

    apiserver_available = "APISERVER_NOT_FOUND" not in stdout and "===KUBEEYE_APISERVER===" in stdout

    # 按块解析各 CRD 数据
    blocks = re.split(r"===CRD:([^=]+)===", stdout)
    i = 1
    while i < len(blocks) - 1:
        crd_name = blocks[i].strip()
        json_str = blocks[i + 1].strip()

        if json_str and json_str != "{}":
            try:
                data = json.loads(json_str)
                items = data.get("items", [])
                for item in items:
                    if crd_name.startswith("inspectresult"):
                        task_info, task_findings = _parse_inspect_result_v1(item, api_result_map, apiserver_available)
                        if task_info:
                            inspect_tasks.append(task_info)
                        results.extend(task_findings)
                    elif crd_name.startswith("inspectplan"):
                        plan_info = _parse_inspect_plan(item)
                        if plan_info:
                            plan_status.append(plan_info)
            except json.JSONDecodeError:
                pass
        i += 2

    # 汇总：优先使用 API 返回的 status.level，回退到 findings 统计
    total_danger = sum(t.get("level_stats", {}).get("danger", 0) for t in inspect_tasks)
    total_warning = sum(t.get("level_stats", {}).get("warning", 0) for t in inspect_tasks)
    total_info = sum(t.get("level_stats", {}).get("ignore", 0) for t in inspect_tasks)

    # 如果 level_stats 全是 0，从 findings 统计
    if total_danger == 0 and total_warning == 0 and results:
        total_danger = sum(1 for r in results if r.get("level") == "error")
        total_warning = sum(1 for r in results if r.get("level") == "warning")
        total_info = sum(1 for r in results if r.get("level") == "info")

    summary = {
        "total": total_danger + total_warning,
        "error": total_danger,
        "warning": total_warning,
        "info": total_info,
    }

    return {
        "crds_found": crds_found,
        "results": results,
        "inspect_tasks": inspect_tasks,
        "plans": plan_status,
        "summary": summary,
    }


def _unwind_api_response(api_result: dict) -> dict:
    """解包 API 响应，返回 InspectResult 对象

    KubeEye API 可能返回：
    - 直接的 InspectResult: {"metadata": {...}, "spec": {...}, "status": {...}}
    - 包装在 data 中: {"code": 0, "data": {...}}
    - 包装在 result 中: {"result": {...}}
    """
    if not isinstance(api_result, dict):
        return {}

    # 如果已经是 InspectResult（有 metadata 和 spec），直接返回
    if "metadata" in api_result and "spec" in api_result:
        return api_result

    # 尝试从 data 字段提取
    for key in ("data", "result", "Data", "Result"):
        if key in api_result:
            inner = api_result[key]
            if isinstance(inner, dict) and "metadata" in inner:
                return inner

    # 尝试从嵌套字段中找
    if "data" in api_result and isinstance(api_result["data"], dict):
        return _unwind_api_response(api_result["data"])

    return {}


def _parse_inspect_result_v1(item: dict, api_result_map: dict = None, apiserver_available: bool = True) -> tuple:
    """解析 KubeEye v1.0 的 InspectResult

    返回: (巡检任务信息, 详细发现列表)

    优先使用 kubeeye-apiserver API 返回的完整数据
    (包含正确的 status.level 和填充的 opaResult)，
    如果 API 不可用则回退到 CRD 中的 opaResult。
    """
    metadata = item.get("metadata", {})
    spec = item.get("spec", {})
    status = item.get("status", {})

    labels = metadata.get("labels", {})
    annotations = metadata.get("annotations", {})
    result_name = metadata.get("name", "")

    # 从 API 结果获取完整数据（如果可用）
    api_result_raw = (api_result_map or {}).get(result_name)
    api_result = _unwind_api_response(api_result_raw) if api_result_raw else {}

    if api_result and apiserver_available:
        api_spec = api_result.get("spec", {})
        api_status = api_result.get("status", {})

        # 使用 API 返回的 level_stats（正确的统计值）
        level_stats = api_status.get("level", {})
        start_time = api_status.get("taskStartTime") or annotations.get("kubeeye.kubesphere.io/task-start-time", "")
        end_time = api_status.get("taskEndTime", "")
        complete = api_status.get("complete", status.get("complete", False))
        policy = api_status.get("policy", status.get("policy", "single"))
        duration = api_status.get("duration", status.get("duration", ""))
        rule_total = api_spec.get("inspectRuleTotal", spec.get("inspectRuleTotal", {}))

        task_info = {
            "name": result_name,
            "plan_name": labels.get("kubeeye.kubesphere.io/plan-name", ""),
            "task_name": labels.get("kubeeye.kubesphere.io/task-name", ""),
            "cluster": api_spec.get("inspectCluster", spec.get("inspectCluster", {})).get("name", "default"),
            "complete": complete,
            "policy": policy,
            "start_time": start_time,
            "end_time": end_time,
            "duration": duration,
            "rule_total": rule_total,
            "level_stats": level_stats,
        }

        # 从 API 结果的 opaResult 提取详细告警
        findings = []
        opa_result = api_spec.get("opaResult", {})
        if opa_result:
            _extract_opa_findings(opa_result, findings, result_name)

        return task_info, findings

    # 回退方案：使用 CRD 数据
    task_info = {
        "name": result_name,
        "plan_name": labels.get("kubeeye.kubesphere.io/plan-name", ""),
        "task_name": labels.get("kubeeye.kubesphere.io/task-name", ""),
        "cluster": spec.get("inspectCluster", {}).get("name", "default"),
        "complete": status.get("complete", False),
        "policy": status.get("policy", "single"),
        "start_time": status.get("taskStartTime") or annotations.get("kubeeye.kubesphere.io/task-start-time", ""),
        "end_time": status.get("taskEndTime", ""),
        "duration": status.get("duration", ""),
        "rule_total": spec.get("inspectRuleTotal", {}),
        "level_stats": status.get("level", {}),
    }

    findings = []
    opa_result = spec.get("opaResult", {})
    if opa_result:
        _extract_opa_findings(opa_result, findings, result_name)

    if not findings:
        _extract_findings_v1(spec, findings, result_name)
        _extract_findings_v1(status, findings, result_name)

    return task_info, findings


def _extract_opa_findings(opa_result: dict, findings: list, task_name: str):
    """从 opaResult 中提取详细发现

    opaResult 可能包含：
      extraInfo: 额外信息
      scoreInfo: 评分信息
      以及其他按资源类型分组的审计结果
    """
    for key, value in opa_result.items():
        if isinstance(value, dict):
            # 检查是否是资源类型的审计结果
            if "resultInfos" in value or "items" in value:
                _extract_findings_v1(value, findings, task_name)
            else:
                _extract_opa_findings(value, findings, task_name)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    _extract_findings_v1(item, findings, task_name)


def _extract_findings_v1(node, findings: list, task_name: str, resource_type: str = ""):
    """递归提取审计发现，适配 KubeEye v1.0 的多种结构"""
    if isinstance(node, dict):
        # 检查是否是审计发现（含 level + message 或 reason）
        if "level" in node and ("message" in node or "reason" in node):
            level = str(node.get("level", "info")).lower()
            # 统一级别映射
            if level in ("critical", "danger", "error"):
                level = "error"
            elif level == "warning":
                level = "warning"
            else:
                level = "info"

            message = str(node.get("message", "") or node.get("Message", "") or "")
            reason = str(node.get("reason", "") or node.get("Reason", "") or "")
            if reason and not message:
                message = reason

            findings.append({
                "task": task_name,
                "resource_type": resource_type or node.get("resourcesType", node.get("resourceType", "")),
                "namespace": node.get("namespace", ""),
                "resource_name": node.get("name", node.get("resourceName", "")),
                "level": level,
                "message": message or reason,
            })
            return

        # 记录资源类型上下文
        current_type = resource_type
        for type_key in ("resourcesType", "resourceType", "type", "kind"):
            if type_key in node and not current_type:
                current_type = str(node[type_key])
                break

        # 递归遍历
        for key, value in node.items():
            if key in ("resultInfos", "resourceInfos", "items", "auditResults", "results"):
                if isinstance(value, list):
                    for item in value:
                        _extract_findings_v1(item, findings, task_name, current_type)
                elif isinstance(value, dict):
                    _extract_findings_v1(value, findings, task_name, current_type)
            elif isinstance(value, (dict, list)):
                _extract_findings_v1(value, findings, task_name, current_type)

    elif isinstance(node, list):
        for item in node:
            _extract_findings_v1(item, findings, task_name, resource_type)


def _parse_inspect_plan(item: dict) -> dict:
    """解析 InspectPlan，提取巡检计划状态"""
    metadata = item.get("metadata", {})
    spec = item.get("spec", {})
    status = item.get("status", {})
    annotations = metadata.get("annotations", {})

    return {
        "name": metadata.get("name", ""),
        "namespace": metadata.get("namespace", ""),
        "schedule": spec.get("schedule", "单次"),
        "suspend": spec.get("suspend", False),
        "max_tasks": spec.get("maxTasks", 0),
        "timeout": spec.get("timeout", "10m"),
        "rule_count": len(spec.get("ruleNames", [])),
        "join_rule_num": annotations.get("kubeeye.kubesphere.io/join-rule-num", ""),
    }


def _enrich_results_from_html(parsed: dict, conn_kwargs: dict, result_names: list, command_timeout: int, debug_info: dict = None):
    """当 API 解析结果为 0 时，通过获取 HTML 报告并解析来补充告警数据"""
    for result_name in result_names:
        safe_name = _sanitize_k8s_name(result_name)
        html_cmd = (
            f'SVC_IP=$(kubectl get svc -n kubeeye-system kubeeye-apiserver '
            f'-o custom-columns=CLUSTER-IP:.spec.clusterIP --no-headers 2>/dev/null | head -1); '
            f'SVC_PORT=$(kubectl get svc -n kubeeye-system kubeeye-apiserver '
            f'-o jsonpath="{{.spec.ports[0].port}}" 2>/dev/null); '
            f'if [ -n "$SVC_IP" ]; then '
            f'  curl -s --max-time 15 '
            f'"http://${{SVC_IP}}:${{SVC_PORT:-9090}}'
            f'/kapis/kubeeye.kubesphere.io/v1alpha2/inspectresults/{safe_name}?type=html" 2>/dev/null; '
            f'fi'
        )

        success, result = _exec(conn_kwargs, html_cmd, command_timeout=command_timeout)

        html_output = result.get("stdout", "") if success else ""

        if debug_info is not None:
            debug_info["html_fetched"] = bool(html_output)
            debug_info["html_length"] = len(html_output)
            # 找到 <table 附近的内容作为调试片段
            if html_output:
                tbl_idx = html_output.lower().find("<table")
                if tbl_idx < 0:
                    tbl_idx = html_output.lower().find("<tr")
                if tbl_idx >= 0:
                    debug_info["html_snippet"] = html_output[tbl_idx:tbl_idx + 1000]
                else:
                    # 没有 table/tr，搜索其他可能包含数据的标签
                    for tag in ["<div", "<span", "<p", "<h"]:
                        idx = html_output.lower().find(tag)
                        if idx > 100:  # 跳过 head 区域
                            debug_info["html_snippet"] = f"(无table标签) 从{tag}开始:\n" + html_output[idx:idx + 1000]
                            break
                    else:
                        debug_info["html_snippet"] = "(未找到任何数据标签) HTML前1000字符:\n" + html_output[:1000]
                # 统计 HTML 中出现的所有标签
                all_tags = set(re.findall(r'<(\w+)', html_output.lower()))
                debug_info["html_tags"] = sorted(all_tags)
            else:
                debug_info["html_snippet"] = "SSH失败: " + result.get("error", "")[:200]

        if not html_output:
            continue

        # 从 HTML 表格解析告警数据
        findings = _parse_html_report_table(html_output, result_name)

        if debug_info is not None:
            debug_info["findings_parsed"] = len(findings)

        if findings:
            parsed["results"].extend(findings)

            # 更新对应任务的 level_stats
            for t in parsed["inspect_tasks"]:
                if t["name"] == result_name:
                    t["level_stats"] = {
                        "danger": sum(1 for f in findings if f.get("level") == "error"),
                        "warning": sum(1 for f in findings if f.get("level") == "warning"),
                        "ignore": sum(1 for f in findings if f.get("level") == "info"),
                    }
                    break

    # 更新汇总统计
    if parsed["results"]:
        total_danger = sum(1 for r in parsed["results"] if r.get("level") == "error")
        total_warning = sum(1 for r in parsed["results"] if r.get("level") == "warning")
        total_info = sum(1 for r in parsed["results"] if r.get("level") == "info")
        parsed["summary"] = {
            "total": total_danger + total_warning,
            "error": total_danger,
            "warning": total_warning,
            "info": total_info,
        }


def _parse_html_report_table(html: str, task_name: str) -> list:
    """从 KubeEye HTML 报告中解析告警数据

    KubeEye 报告使用 <div class="table/tr/td"> 模拟表格，而非原生 <table>/<tr>/<td>。
    """
    findings = []

    # 方案 1: 解析 div 模拟表格（KubeEye 实际格式）
    # 按 <div class="tr"> 分割行
    tr_splits = re.split(r'<div\s+class="[^"]*\btr\b[^"]*"[^>]*>', html, flags=re.IGNORECASE)
    if len(tr_splits) > 2:  # 至少有表头 + 1 行数据
        for i, tr_content in enumerate(tr_splits[1:], 0):  # 跳过 tr 之前的内容
            # 提取所有 <div class="td">...</div>
            td_pattern = re.compile(
                r'<div\s+class="[^"]*\btd\b[^"]*"[^>]*>(.*?)</div>',
                re.DOTALL | re.IGNORECASE
            )
            raw_cells = td_pattern.findall(tr_content)
            if not raw_cells:
                continue
            # 清理嵌套 HTML 标签
            clean_cells = [re.sub(r"<[^>]+>", "", c).strip() for c in raw_cells]
            if not clean_cells or all(not c for c in clean_cells):
                continue
            # 跳过表头
            if i == 0 and clean_cells[0].lower() in ("name", "名称"):
                continue
            _process_row_cells(clean_cells, task_name, findings)

        if findings:
            return findings

    # 方案 2: 解析原生 <table>/<tr>/<td> 标签（其他格式兼容）
    try:
        from html.parser import HTMLParser

        class _TableParser(HTMLParser):
            def __init__(self):
                super().__init__()
                self.tables = []
                self.current_table = []
                self.current_row = []
                self.current_cell = ""
                self.in_table = False
                self.in_td = False
                self.in_th = False

            def handle_starttag(self, tag, attrs):
                tag = tag.lower()
                if tag == "table":
                    self.in_table = True
                    self.current_table = []
                elif tag == "tr" and self.in_table:
                    self.current_row = []
                elif tag == "td" and self.in_table:
                    self.in_td = True
                    self.current_cell = ""
                elif tag == "th" and self.in_table:
                    self.in_th = True
                    self.current_cell = ""

            def handle_endtag(self, tag):
                tag = tag.lower()
                if tag == "table" and self.in_table:
                    if self.current_table:
                        self.tables.append(self.current_table)
                    self.in_table = False
                elif tag == "tr" and self.in_table:
                    if self.current_row:
                        self.current_table.append(self.current_row)
                    self.current_row = []
                elif tag == "td" and self.in_td:
                    self.current_row.append(self.current_cell.strip())
                    self.in_td = False
                elif tag == "th" and self.in_th:
                    self.current_row.append(self.current_cell.strip())
                    self.in_th = False

            def handle_data(self, data):
                if self.in_td or self.in_th:
                    self.current_cell += data

        parser = _TableParser()
        parser.feed(html)

        for table in parser.tables:
            for i, row in enumerate(table):
                if not row:
                    continue
                if i == 0 and row and row[0].lower() in ("name", "名称"):
                    continue
                _process_row_cells(row, task_name, findings)
            if findings:
                break

        if findings:
            return findings
    except Exception:
        pass

    # 方案 3: 正则回退
    rows = re.split(r"</tr\s*>", html, flags=re.IGNORECASE)
    for i, row in enumerate(rows):
        if not row.strip():
            continue
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.DOTALL | re.IGNORECASE)
        if not cells:
            continue
        clean_cells = [re.sub(r"<[^>]+>", "", c).strip() for c in cells]
        if i == 0 and clean_cells and clean_cells[0].lower() in ("name", "名称"):
            continue
        _process_row_cells(clean_cells, task_name, findings)

    return findings


def _process_row_cells(clean_cells: list, task_name: str, findings: list):
    """处理一行表格数据，提取告警信息"""
    if len(clean_cells) < 5:
        return

    if len(clean_cells) >= 6:
        name, kind, namespace, message, reason, level = clean_cells[:6]
    elif len(clean_cells) >= 5:
        kind = clean_cells[0]
        namespace = clean_cells[1]
        message = clean_cells[2]
        reason = clean_cells[3]
        level = clean_cells[4]
        name = ""
    else:
        return

    level_lower = level.lower().strip()
    if level_lower in ("critical", "danger", "error", "严重"):
        level_mapped = "error"
    elif level_lower in ("warning", "警告"):
        level_mapped = "warning"
    else:
        level_mapped = "info"

    if not message and reason:
        message = reason

    findings.append({
        "task": task_name,
        "resource_type": kind,
        "namespace": namespace,
        "resource_name": name,
        "level": level_mapped,
        "message": message or reason,
        "reason": reason,
    })
