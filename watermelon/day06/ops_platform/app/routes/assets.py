import asyncio
import codecs
import json
import logging
import posixpath
import re
from io import BytesIO
from urllib.parse import quote, unquote

from fastapi import APIRouter, Depends, HTTPException, Request, status, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from typing import Optional, List

from app.database import get_db, SessionLocal
from app.schemas import (
    AssetCreate, AssetUpdate, AssetResponse,
    SSHTestRequest, SSHTestResponse, SSHCommandRequest, SSHCommandResponse
)
from app.crud import (
    get_assets, get_asset, get_asset_by_hostname,
    create_asset, update_asset, delete_asset, update_asset_status,
    has_remote_permission, get_remote_command_policy,
    get_user_allowed_asset_ids,
)
from app.models import User
from app.security import get_current_user, parse_access_token, require_admin
from app.services.ssh_client import (
    SSHClient,
    test_server_connection,
    execute_remote_command,
    upload_remote_file,
    download_remote_file,
    list_remote_dir,
)
from app.services.crypto import decrypt_password, decrypt_secret
from app.services.audit import (
    write_audit_log, ACTION_CREATE, ACTION_UPDATE, ACTION_DELETE,
    ACTION_EXECUTE, ACTION_UPLOAD, ACTION_DOWNLOAD, ACTION_TEST,
    ACTION_TERMINAL, ACTION_BROWSE, RESOURCE_ASSET,
)

router = APIRouter(prefix="/api/assets", tags=["资产管理"])


def _mask_sensitive_command(cmd: str) -> str:
    """对审计日志中的命令做脱敏：屏蔽 sudo -S 后面的密码、echo 'PWD' 等模式"""
    if not cmd:
        return cmd
    # 屏蔽 echo 'xxx' | sudo -S 模式中的密码
    masked = re.sub(
        r"echo\s+'[^']*'\s*\|\s*sudo\s+-S",
        "echo '***' | sudo -S",
        cmd,
    )
    # 屏蔽 sudo -S 后面的密码（如 sudo -S -p 'pwd'）
    masked = re.sub(
        r"sudo\s+-S\s+-p\s+'[^']*'",
        "sudo -S -p '***'",
        masked,
    )
    return masked


def _strip_terminal_noise(text: str) -> str:
    """过滤容易被部分终端误显示的控制序列，保留正常 ANSI 彩色和光标控制"""
    text = re.sub(r"\x1b\[\?2004[hl]", "", text)
    text = re.sub(r"\x1b\](?:0|1|2);[^\x07\x1b]*(?:\x07|\x1b\\)", "", text)
    return text


def _asset_connection_kwargs(db_asset):
    """提取资产 SSH 连接参数，并解密密码 / 私钥 / 证书字段"""
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
            "expected_host_key": db_asset.expected_host_key,
        }
    except ValueError as e:
        # 解密失败：密钥不匹配或数据损坏，不回退明文，拒绝请求
        raise HTTPException(status_code=500, detail=f"资产敏感字段解密失败，请联系管理员重新配置: {e}")


import base64 as _b64

# 命令分隔符：; & | ( ) ` 以及行首/空格
# 注意：$ 和 = 也作为分隔符，防止 X=rm; $X 这种变量替换绕过
_SEP = r"(^|[;&|()<>`\s=$]+)"

# 极高危命令关键字（始终拦截，不受 allow_sensitive_commands 影响）
_HIGH_RISK_PATTERNS = [
    (re.compile(_SEP + r"(?:\S*/)?rm\b"), "禁止执行 rm 删除类高危命令"),
    (re.compile(_SEP + r"(?:\S*/)?(?:mkfs|fdisk|parted|wipefs)\b"), "禁止执行磁盘格式化或分区类高危命令"),
    (re.compile(_SEP + r"(?:\S*/)?dd\s+[^;&|]*(of=/dev/|if=/dev/zero)"), "禁止执行可能覆盖磁盘设备的 dd 命令"),
    (re.compile(_SEP + r"(?:\S*/)?shutdown\b"), "禁止执行关机命令"),
    (re.compile(_SEP + r"(?:\S*/)?reboot\b"), "禁止执行重启命令"),
    (re.compile(_SEP + r"(?:\S*/)?halt\b"), "禁止执行停机命令"),
    (re.compile(_SEP + r"(?:\S*/)?init\s+0\b"), "禁止执行关机命令"),
    (re.compile(_SEP + r"(?:\S*/)?poweroff\b"), "禁止执行关机命令"),
]

# 中危命令关键字（allow_sensitive_commands=True 时放行）
_MEDIUM_RISK_PATTERNS = [
    (re.compile(_SEP + r"(?:\S*/)?chmod\b"), "禁止执行 chmod 修改权限命令"),
    (re.compile(_SEP + r"(?:\S*/)?chown\b"), "禁止执行 chown 修改属主命令"),
]

# shell 解释器及其 -c 参数中可能隐藏的危险命令
_SHELL_INTERPRETERS = {
    "sh", "bash", "dash", "zsh", "ksh", "ash", "csh", "tcsh", "fish",
    "$shell", "$0", "/bin/sh", "/bin/bash", "/usr/bin/bash",
    "/sbin/sh", "/usr/bin/sh", "/usr/local/bin/bash",
    "python", "python2", "python3", "perl", "ruby", "php", "node", "lua",
    "/usr/bin/python", "/usr/bin/python3", "/usr/bin/perl",
    # 新增：可执行任意命令的命令
    "eval", "exec", "source", ".",
    "xargs", "awk", "sed", "gawk", "mawk",
}

# 命令前缀（类似 sudo/nohup，需要剥离后再检查）
_COMMAND_PREFIXES = [
    r"(sudo\s+)+",
    r"(nohup\s+)+",
    r"(env\s+\w+=\S+\s+)+",   # env VAR=value cmd（仅匹配 VAR=value 形式，不再吞命令名）
    r"(timeout\s+\S+\s+)+",
    r"(nice\s+)+",
    r"(ionice\s+\S+\s+)+",
]


def _normalize_command(text: str) -> str:
    """命令规范化：合并续行、合并多空格、统一大小写"""
    if not text:
        return ""
    # 合并反斜杠续行（\ + 换行 → 空格），防止拆行绕过
    text = text.replace("\\\n", " ").replace("\\\r\n", " ")
    # 合并多空格
    text = re.sub(r"\s+", " ", text.strip())
    return text


def _strip_quotes_and_escapes(text: str) -> str:
    """去除引号和反斜杠转义，还原命令本貌

    处理：
    - 双引号、单引号（跳过引号字符本身）
    - 反斜杠转义（保留下一个字符）
    - ANSI-C 引号 $'...'（剥离 $' 和 '，保留中间内容）
    """
    result = []
    i = 0
    while i < len(text):
        ch = text[i]
        # ANSI-C 引号 $'...' → 保留内容
        if ch == '$' and i + 1 < len(text) and text[i + 1] == "'":
            # 找到匹配的 '
            end = text.find("'", i + 2)
            if end != -1:
                result.append(text[i + 2:end])
                i = end + 1
                continue
        if ch == '\\' and i + 1 < len(text):
            # 反斜杠转义：保留下一个字符
            result.append(text[i + 1])
            i += 2
            continue
        if ch in ('"', "'"):
            # 跳过引号字符本身
            i += 1
            continue
        result.append(ch)
        i += 1
    return ''.join(result)


def _expand_shell_substitutions(text: str) -> str:
    """展开 shell 变量替换和命令替换，还原命令本貌

    处理形式：
    - $VAR → 空字符串（无法展开，视为空）
    - ${VAR} → 空字符串
    - $(cmd) → cmd（提取命令替换内容）
    - `cmd` → cmd（提取反引号命令替换内容）
    - $'...' → ...（已在 _strip_quotes_and_escapes 处理）
    """
    # 处理 $(...) 命令替换：$(echo rm) → echo rm
    text = re.sub(r'\$\(([^)]*)\)', r'\1', text)
    # 处理反引号命令替换：`echo rm` → echo rm
    text = re.sub(r'`([^`]*)`', r'\1', text)
    # 处理 $VAR 和 ${VAR}：直接移除（无法展开，使模式不匹配变量引用）
    text = re.sub(r'\$\{?\w+\}?', '', text)
    return text


def _strip_prefixes(text: str) -> str:
    """剥离 sudo/nohup/env/time/nice 等命令前缀"""
    changed = True
    while changed:
        changed = False
        for prefix in _COMMAND_PREFIXES:
            new_text = re.sub(r"^" + prefix, "", text, flags=re.IGNORECASE)
            if new_text != text:
                text = new_text
                changed = True
    return text


def _extract_shell_c_subcommands(text: str) -> list:
    """从 bash -c 'xxx' / sh -c "xxx" 中提取子命令字符串"""
    subs = []
    # 匹配 shell -c 'cmd' 或 shell -c "cmd" 或 shell -c cmd
    pattern = re.compile(
        r'(?:^|[;&|]\s*)(?:\S*/)?(?:' + '|'.join(
            re.escape(s) for s in _SHELL_INTERPRETERS
        ) + r')\s+(?:-\w+\s+)*-c\s+(["\'])(.*?)\1',
        re.IGNORECASE
    )
    for m in pattern.finditer(text):
        subs.append(m.group(2))
    # 不带引号的 -c 后面跟裸命令（直到 ; | & 或行尾）
    pattern2 = re.compile(
        r'(?:^|[;&|]\s*)(?:\S*/)?(?:' + '|'.join(
            re.escape(s) for s in _SHELL_INTERPRETERS
        ) + r')\s+(?:-\w+\s+)*-c\s+([^\s;&|]+)',
        re.IGNORECASE
    )
    for m in pattern2.finditer(text):
        subs.append(m.group(1))
    return subs


def _extract_base64_payloads(text: str) -> list:
    """检测 base64 编码的命令并解码返回

    覆盖形式：
    - echo <base64> | base64 -d/--decode/-D | bash
    - printf <base64> | base64 -d/--decode/-D | bash
    - base64 -d <<< <base64>
    - $B=<base64>; echo $B | base64 -d
    """
    payloads = []
    # base64 解码参数：-d / --decode / -D
    b64_decode = r'(?:-d|--decode|-D)'

    # echo <b64> | base64 -d ...
    for m in re.finditer(r'(?:echo|printf)\s+([A-Za-z0-9+/=]{4,})\s*\|\s*base64\s+' + b64_decode, text):
        try:
            decoded = _b64.b64decode(m.group(1)).decode('utf-8', errors='ignore')
            if decoded.strip():
                payloads.append(decoded)
        except Exception:
            pass
    # $(echo <b64> | base64 -d) 或变量形式
    for m in re.finditer(r'\$?\(?(?:echo|printf)\s+([A-Za-z0-9+/=]{4,})\s*\|\s*base64\s+' + b64_decode, text):
        try:
            decoded = _b64.b64decode(m.group(1)).decode('utf-8', errors='ignore')
            if decoded.strip():
                payloads.append(decoded)
        except Exception:
            pass
    # base64 -d <<< <b64>
    for m in re.finditer(r'base64\s+' + b64_decode + r'\s*<<<\s*([A-Za-z0-9+/=]{4,})', text):
        try:
            decoded = _b64.b64decode(m.group(1)).decode('utf-8', errors='ignore')
            if decoded.strip():
                payloads.append(decoded)
        except Exception:
            pass
    # 变量中转：B=<b64>; echo $B | base64 -d
    for m in re.finditer(r'\b(\w+)\s*=\s*([A-Za-z0-9+/=]{4,})\b.*?\$\1\s*\|\s*base64\s+' + b64_decode, text):
        try:
            decoded = _b64.b64decode(m.group(2)).decode('utf-8', errors='ignore')
            if decoded.strip():
                payloads.append(decoded)
        except Exception:
            pass
    return payloads


def _check_dangerous_patterns(text: str, patterns: list) -> Optional[str]:
    """对预处理后的文本逐条匹配危险模式"""
    for pat, reason in patterns:
        if pat.search(text):
            return reason
    return None


def _get_sensitive_command_reason(
    command: str,
    allow_sensitive_commands: bool = False,
    allow_high_risk_commands: bool = False,
    blocked_commands: str = "",
) -> Optional[str]:
    """根据权限策略识别需要拦截的命令，命中时返回拦截原因

    分层防御：
    1. 自定义拦截规则 — 始终生效
    2. 极高危命令检查（allow_high_risk_commands=True 时跳过）
    3. 提取 bash -c / sh -c 子命令后递归检查
    4. 检测 base64 解码后的命令内容
    5. 若 allow_sensitive_commands=True，跳过中危命令
    6. 中危命令检查

    预处理层（在所有检查前应用）：
    - 合并反斜杠续行
    - 去除引号、转义
    - 展开 $VAR / ${VAR} / $(cmd) / `cmd`
    - 剥离 sudo/nohup/env VAR=value/time/nice 前缀
    """
    normalized = _normalize_command(command or "")
    if not normalized:
        return None

    # 预处理：去除引号、转义
    stripped = _strip_quotes_and_escapes(normalized)
    # 展开 shell 变量替换和命令替换
    expanded = _expand_shell_substitutions(stripped)
    # 剥离命令前缀
    stripped_final = _strip_prefixes(expanded)

    lowered_raw = normalized.lower()
    lowered_clean = stripped_final.lower()

    # 要检查的文本集合：原始 + 去引号展开后
    texts_to_check = [lowered_raw, lowered_clean]

    # --- 第1层：自定义拦截规则，始终生效 ---
    for rule in re.split(r"[\n,;]+", blocked_commands or ""):
        rule = rule.strip()
        if not rule:
            continue
        if rule.startswith("re:"):
            try:
                if re.search(rule[3:], normalized, flags=re.IGNORECASE):
                    return f"命中自定义拦截规则: {rule}"
            except re.error:
                continue
        elif rule.lower() in lowered_clean:
            return f"命中自定义拦截规则: {rule}"

    # --- 第2层：极高危命令检查（allow_high_risk_commands=True 时跳过） ---
    if not allow_high_risk_commands:
        for text in texts_to_check:
            reason = _check_dangerous_patterns(text, _HIGH_RISK_PATTERNS)
            if reason:
                return reason

    # --- 第3层：提取 shell -c 子命令后递归检查 ---
    for subcmd in _extract_shell_c_subcommands(normalized):
        sub_reason = _get_sensitive_command_reason(
            subcmd,
            allow_sensitive_commands=allow_sensitive_commands,
            allow_high_risk_commands=allow_high_risk_commands,
            blocked_commands=blocked_commands,
        )
        if sub_reason:
            return f"通过 shell -c 间接执行高危命令被拦截: {sub_reason}"

    # --- 第4层：检测 base64 解码后的命令内容 ---
    for payload in _extract_base64_payloads(normalized):
        sub_reason = _get_sensitive_command_reason(
            payload,
            allow_sensitive_commands=allow_sensitive_commands,
            allow_high_risk_commands=allow_high_risk_commands,
            blocked_commands=blocked_commands,
        )
        if sub_reason:
            return f"通过 base64 编码隐藏高危命令被拦截: {sub_reason}"

    # --- 第5层：如果允许中危命令或高危命令，跳过中危检查 ---
    if allow_sensitive_commands or allow_high_risk_commands:
        return None

    # --- 第6层：中危命令检查 ---
    for text in texts_to_check:
        reason = _check_dangerous_patterns(text, _MEDIUM_RISK_PATTERNS)
        if reason:
            return reason
    return None


@router.get("/", response_model=List[AssetResponse])
def list_assets(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    env: Optional[str] = None,
    role: Optional[str] = None,
    status: Optional[str] = None,
    search: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    # 防越权枚举：非管理员仅能查看自己有权限的资产
    allowed_ids = get_user_allowed_asset_ids(db, current_user)
    return get_assets(
        db, skip, limit, env, role, status, search,
        allowed_asset_ids=allowed_ids,
    )


@router.post("/", response_model=AssetResponse, status_code=status.HTTP_201_CREATED)
def create_new_asset(
    asset: AssetCreate,
    req: Request,
    db: Session = Depends(get_db),
    current_admin: User = Depends(require_admin)
):
    existing = get_asset_by_hostname(db, asset.hostname)
    if existing:
        raise HTTPException(status_code=400, detail=f"主机名 '{asset.hostname}' 已存在")
    result = create_asset(db, asset)
    write_audit_log(
        db, username=current_admin.username, action=ACTION_CREATE, resource_type=RESOURCE_ASSET,
        resource_name=asset.hostname, detail=f"创建资产: {asset.hostname} ({asset.ip})",
        ip_address=req.client.host if req.client else None,
    )
    return result


@router.get("/{asset_id}", response_model=AssetResponse)
def get_asset_detail(
    asset_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    db_asset = get_asset(db, asset_id)
    if not db_asset:
        raise HTTPException(status_code=404, detail="资产不存在")
    # 防越权：非管理员访问无权限资产也返回 404，避免枚举资产存在性
    if current_user.role != "admin" and not has_remote_permission(db, current_user.id, asset_id, "connect"):
        raise HTTPException(status_code=404, detail="资产不存在")
    return db_asset


@router.put("/{asset_id}", response_model=AssetResponse)
def update_asset_detail(
    asset_id: int,
    asset: AssetUpdate,
    req: Request,
    db: Session = Depends(get_db),
    current_admin: User = Depends(require_admin)
):
    db_asset = update_asset(db, asset_id, asset)
    if not db_asset:
        raise HTTPException(status_code=404, detail="资产不存在")
    write_audit_log(
        db, username=current_admin.username, action=ACTION_UPDATE, resource_type=RESOURCE_ASSET,
        resource_name=db_asset.hostname, detail=f"更新资产: {db_asset.hostname}",
        ip_address=req.client.host if req.client else None,
    )
    return db_asset


@router.delete("/{asset_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_asset_detail(
    asset_id: int,
    req: Request,
    db: Session = Depends(get_db),
    current_admin: User = Depends(require_admin)
):
    db_asset = get_asset(db, asset_id)
    if not db_asset:
        raise HTTPException(status_code=404, detail="资产不存在")
    hostname = db_asset.hostname
    if not delete_asset(db, asset_id):
        raise HTTPException(status_code=404, detail="资产不存在")
    write_audit_log(
        db, username=current_admin.username, action=ACTION_DELETE, resource_type=RESOURCE_ASSET,
        resource_name=hostname, detail=f"删除资产: {hostname}",
        ip_address=req.client.host if req.client else None,
    )


@router.patch("/{asset_id}/status", response_model=AssetResponse)
def update_asset_status_route(
    asset_id: int,
    status: str = Query(..., description="online/offline/unknown"),
    db: Session = Depends(get_db),
    current_admin: User = Depends(require_admin)
):
    valid_statuses = ["online", "offline", "unknown"]
    if status not in valid_statuses:
        raise HTTPException(status_code=400, detail=f"状态必须是: {', '.join(valid_statuses)}")
    db_asset = update_asset_status(db, asset_id, status)
    if not db_asset:
        raise HTTPException(status_code=404, detail="资产不存在")
    return db_asset


# ============================================================
# SSH远程连接相关接口
# ============================================================

@router.post("/{asset_id}/test-connection", response_model=SSHTestResponse)
def test_ssh_connection(
    asset_id: int,
    request: SSHTestRequest,
    req: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """测试SSH连接是否可用"""
    db_asset = get_asset(db, asset_id)
    if not db_asset:
        raise HTTPException(status_code=404, detail="资产不存在")

    if not has_remote_permission(db, current_user.id, asset_id, "connect"):
        raise HTTPException(status_code=403, detail="当前用户没有该服务器的远程连接权限")

    if not db_asset.password and not db_asset.ssh_key and not db_asset.ssh_key_path:
        raise HTTPException(status_code=400, detail="该资产未配置密码、SSH密钥或密钥文件路径，无法连接")

    success, message = test_server_connection(
        **_asset_connection_kwargs(db_asset),
        timeout=request.timeout
    )

    # 更新服务器状态
    new_status = "online" if success else "offline"
    update_asset_status(db, asset_id, new_status)

    write_audit_log(
        db, username=current_user.username, action=ACTION_TEST, resource_type=RESOURCE_ASSET,
        resource_name=db_asset.hostname, detail=f"测试连接: {db_asset.hostname} - {'成功' if success else '失败'}",
        ip_address=req.client.host if req.client else None, status="success" if success else "failure",
    )

    return SSHTestResponse(
        success=success,
        message=message,
        hostname=db_asset.hostname,
        ip=db_asset.ip
    )


@router.post("/{asset_id}/execute", response_model=SSHCommandResponse)
def execute_ssh_command(
    asset_id: int,
    request: SSHCommandRequest,
    req: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """在远程服务器上执行命令"""
    db_asset = get_asset(db, asset_id)
    if not db_asset:
        raise HTTPException(status_code=404, detail="资产不存在")

    if not has_remote_permission(db, current_user.id, asset_id, "connect"):
        raise HTTPException(status_code=403, detail="当前用户没有该服务器的远程连接权限")

    command_policy = get_remote_command_policy(db, current_user.id, asset_id)
    sensitive_reason = _get_sensitive_command_reason(
        request.command,
        allow_sensitive_commands=command_policy["allow_sensitive_commands"],
        allow_high_risk_commands=command_policy["allow_high_risk_commands"],
        blocked_commands=command_policy["blocked_commands"],
    )
    if sensitive_reason:
        write_audit_log(
            db, username=current_user.username, action=ACTION_EXECUTE, resource_type=RESOURCE_ASSET,
            resource_name=db_asset.hostname, detail=f"拦截高危命令: {_mask_sensitive_command(request.command)}，原因: {sensitive_reason}",
            ip_address=req.client.host if req.client else None, status="failure",
        )
        raise HTTPException(status_code=403, detail=sensitive_reason)

    if not db_asset.password and not db_asset.ssh_key and not db_asset.ssh_key_path:
        raise HTTPException(status_code=400, detail="该资产未配置密码、SSH密钥或密钥文件路径，无法连接")

    success, result = execute_remote_command(
        **_asset_connection_kwargs(db_asset),
        command=request.command,
        command_timeout=request.timeout
    )

    write_audit_log(
        db, username=current_user.username, action=ACTION_EXECUTE, resource_type=RESOURCE_ASSET,
        resource_name=db_asset.hostname, detail=f"执行命令: {_mask_sensitive_command(request.command)}",
        ip_address=req.client.host if req.client else None, status="success" if success else "failure",
    )

    response = SSHCommandResponse(
        success=success,
        hostname=db_asset.hostname,
        command=request.command
    )

    if success:
        response.stdout = result.get('stdout', '')
        response.stderr = result.get('stderr', '')
        response.exit_code = result.get('exit_code')
    else:
        response.error = result.get('error', '未知错误')

    return response


# SFTP 敏感路径保护：禁止访问系统关键文件和 SSH 私钥目录
# 黑名单方式：兼顾运维场景灵活性，仅禁止明显的敏感系统路径
_SENSITIVE_PATH_PATTERNS = [
    re.compile(r"(?:^|/)\.ssh/(?:id_|authorized_keys|known_hosts|config)", re.IGNORECASE),
    re.compile(r"(?:^|/)etc/(?:shadow|passwd|sudoers|gshadow)(?:$|/)", re.IGNORECASE),
    re.compile(r"(?:^|/)etc/ssh/", re.IGNORECASE),
    re.compile(r"(?:^|/)root/\.ssh/", re.IGNORECASE),
    re.compile(r"(?:^|/)boot/(?:grub|efi|vmlinuz|initramfs)", re.IGNORECASE),
    re.compile(r"(?:^|/)proc/(?:self|sys)", re.IGNORECASE),
]


def _validate_sftp_path(remote_path: str, action: str = "access") -> None:
    """校验 SFTP 路径，禁止访问敏感系统路径。

    防止通过平台下载 SSH 私钥、shadow 文件等导致横向移动凭据泄露，
    或上传覆盖系统关键文件导致服务器被破坏。
    """
    if not remote_path:
        return
    # 标准化路径：解析 .. / . / 重复斜杠，避免绕过
    normalized = posixpath.normpath(remote_path)
    for pat in _SENSITIVE_PATH_PATTERNS:
        if pat.search(normalized):
            raise HTTPException(
                status_code=403,
                detail=f"禁止{action}敏感路径: {normalized}（SSH 私钥/系统配置等敏感文件受保护）",
            )


@router.post("/{asset_id}/upload")
async def upload_asset_file(
    asset_id: int,
    request: Request,
    remote_dir: Optional[str] = Query(default=None),
    remote_name: Optional[str] = Query(default=None),
    remote_path: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """通过 SFTP 上传文件到远程服务器"""
    db_asset = get_asset(db, asset_id)
    if not db_asset:
        raise HTTPException(status_code=404, detail="资产不存在")
    if not has_remote_permission(db, current_user.id, asset_id, "connect"):
        raise HTTPException(status_code=403, detail="当前用户没有该服务器的文件传输权限")
    file_data = await request.body()
    if not file_data:
        raise HTTPException(status_code=400, detail="上传文件内容为空")

    if remote_path:
        final_remote_path = remote_path.strip()
        if final_remote_path.endswith("/"):
            raise HTTPException(status_code=400, detail="远程完整路径不能以 / 结尾")
    else:
        upload_dir = (remote_dir or "").strip()
        if not upload_dir:
            raise HTTPException(status_code=400, detail="请填写上传目录")
        original_filename = unquote(request.headers.get("x-filename", "")).strip()
        upload_name = (remote_name or "").strip() or original_filename
        if not upload_name:
            raise HTTPException(status_code=400, detail="无法识别文件名，请填写上传后的文件名")
        if "/" in upload_name or "\\" in upload_name:
            raise HTTPException(status_code=400, detail="上传后的文件名不能包含路径分隔符")
        final_remote_path = posixpath.join(upload_dir.rstrip("/") or "/", upload_name)

    # 路径白名单校验：禁止覆盖敏感系统文件
    _validate_sftp_path(final_remote_path, "上传到")

    success, message = upload_remote_file(
        file_obj=BytesIO(file_data),
        remote_path=final_remote_path,
        **_asset_connection_kwargs(db_asset)
    )
    if not success:
        raise HTTPException(status_code=400, detail=message)
    write_audit_log(
        db, username=current_user.username, action=ACTION_UPLOAD, resource_type=RESOURCE_ASSET,
        resource_name=db_asset.hostname, detail=f"上传文件: {final_remote_path}",
        ip_address=request.client.host if request.client else None,
    )
    return {"success": True, "message": message, "remote_path": final_remote_path}


@router.get("/{asset_id}/download")
def download_asset_file(
    asset_id: int,
    remote_path: str = Query(...),
    req: Request = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """通过 SFTP 从远程服务器下载文件"""
    db_asset = get_asset(db, asset_id)
    if not db_asset:
        raise HTTPException(status_code=404, detail="资产不存在")
    if not has_remote_permission(db, current_user.id, asset_id, "connect"):
        raise HTTPException(status_code=403, detail="当前用户没有该服务器的文件传输权限")
    if not remote_path or remote_path.strip().endswith("/"):
        raise HTTPException(status_code=400, detail="请填写远程文件完整路径")

    # 路径白名单校验：禁止下载 SSH 私钥 / shadow 等敏感凭据文件
    _validate_sftp_path(remote_path.strip(), "下载")

    success, result = download_remote_file(
        remote_path=remote_path.strip(),
        **_asset_connection_kwargs(db_asset)
    )
    if not success:
        raise HTTPException(status_code=400, detail=result.get("error", "下载失败"))

    write_audit_log(
        db, username=current_user.username, action=ACTION_DOWNLOAD, resource_type=RESOURCE_ASSET,
        resource_name=db_asset.hostname, detail=f"下载文件: {remote_path.strip()}",
        ip_address=req.client.host if req and req.client else None,
    )

    filename = result.get("filename") or "download.bin"
    headers = {
        "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"
    }
    return StreamingResponse(
        BytesIO(result.get("data", b"")),
        media_type="application/octet-stream",
        headers=headers
    )


@router.get("/{asset_id}/list-dir")
def list_asset_dir(
    asset_id: int,
    remote_path: str = Query(default="."),
    req: Request = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """通过 SFTP 列出远程服务器目录内容"""
    db_asset = get_asset(db, asset_id)
    if not db_asset:
        raise HTTPException(status_code=404, detail="资产不存在")
    if not has_remote_permission(db, current_user.id, asset_id, "connect"):
        raise HTTPException(status_code=403, detail="当前用户没有该服务器的文件传输权限")

    success, result = list_remote_dir(
        remote_path=remote_path.strip() or ".",
        **_asset_connection_kwargs(db_asset)
    )
    if not success:
        raise HTTPException(status_code=400, detail=result.get("error", "列出目录失败"))
    write_audit_log(
        db, username=current_user.username, action=ACTION_BROWSE, resource_type=RESOURCE_ASSET,
        resource_name=db_asset.hostname, detail=f"浏览目录: {result.get('path', remote_path)}",
        ip_address=req.client.host if req and req.client else None,
    )
    return result


@router.websocket("/{asset_id}/terminal")
async def ssh_terminal(asset_id: int, websocket: WebSocket):
    """WebSocket 交互式 SSH 终端

    认证：通过首条消息传递 Token，避免 Token 出现在 URL / 访问日志 / 代理日志中。
    首条消息格式（JSON）：{"type": "auth", "token": "<access_token>"}
    或纯字符串："Bearer <access_token>"
    """
    await websocket.accept()
    db = SessionLocal()
    ssh = SSHClient(timeout=10)
    channel = None
    # 捕获审计日志需要的字段（在 db 关闭之前）
    audit_username = None
    audit_hostname = None
    audit_ip = None
    command_policy = {"allow_sensitive_commands": False, "blocked_commands": ""}
    _ws_logger = logging.getLogger(__name__)
    try:
        try:
            # 等待首条消息作为认证（10 秒超时）
            try:
                first_msg = await asyncio.wait_for(websocket.receive_text(), timeout=10)
            except asyncio.TimeoutError:
                await websocket.send_text("\r\n[错误] 认证超时，请重新连接\r\n")
                await websocket.close()
                return
            except WebSocketDisconnect:
                return
            # 解析 auth 消息：支持 JSON 或 "Bearer xxx" 纯字符串
            token = ""
            try:
                auth_data = json.loads(first_msg)
                if isinstance(auth_data, dict) and auth_data.get("type") == "auth":
                    token = str(auth_data.get("token") or "")
            except (json.JSONDecodeError, TypeError):
                # 兼容纯字符串形式
                if isinstance(first_msg, str) and first_msg.startswith("Bearer "):
                    token = first_msg.removeprefix("Bearer ").strip()
            if not token:
                _ws_logger.warning(f"WebSocket 终端认证失败：首条消息不是合法 auth 消息")
                await websocket.send_text("\r\n[错误] 认证失败：未提供合法 Token，请重新登录\r\n")
                await websocket.close()
                return
            payload = parse_access_token(token)
            current_user = db.query(User).filter(User.id == payload.get("user_id")).first()
            if not current_user or not current_user.is_active:
                await websocket.send_text("\r\n[错误] 用户不存在或已停用\r\n")
                await websocket.close()
                return
            # 校验 token_version（与 HTTP 接口一致，改密后旧 Token 立即失效）
            if int(payload.get("v", 0)) != int(current_user.token_version or 0):
                await websocket.send_text("\r\n[错误] 登录已失效，请重新登录\r\n")
                await websocket.close()
                return
            db_asset = get_asset(db, asset_id)
            if not db_asset:
                await websocket.send_text("\r\n[错误] 资产不存在\r\n")
                await websocket.close()
                return
            if not has_remote_permission(db, current_user.id, asset_id, "connect"):
                await websocket.send_text("\r\n[错误] 当前用户没有该服务器的远程连接权限\r\n")
                await websocket.close()
                return
            command_policy = get_remote_command_policy(db, current_user.id, asset_id)
            connection_kwargs = _asset_connection_kwargs(db_asset)
            audit_username = current_user.username
            audit_hostname = db_asset.hostname
            audit_ip = websocket.client.host if websocket.client else None
        except HTTPException as e:
            await websocket.send_text(f"\r\n[错误] {e.detail}\r\n")
            await websocket.close()
            return
        finally:
            db.close()

        await websocket.send_text("[正在建立 SSH 交互会话...]\r\n")
        success, message = await asyncio.to_thread(ssh.connect, **connection_kwargs)
        if not success:
            await websocket.send_text(f"[连接失败] {message}\r\n")
            # 写入审计日志：终端连接失败
            if audit_username and audit_hostname:
                audit_db = SessionLocal()
                try:
                    write_audit_log(
                        audit_db, username=audit_username, action=ACTION_TERMINAL,
                        resource_type=RESOURCE_ASSET, resource_name=audit_hostname,
                        detail=f"终端连接失败: {message}",
                        ip_address=audit_ip, status="failure",
                    )
                finally:
                    audit_db.close()
            await websocket.close()
            return

        # 写入审计日志：终端连接成功
        if audit_username and audit_hostname:
            audit_db = SessionLocal()
            try:
                write_audit_log(
                    audit_db, username=audit_username, action=ACTION_TERMINAL,
                    resource_type=RESOURCE_ASSET, resource_name=audit_hostname,
                    detail=f"终端会话已建立",
                    ip_address=audit_ip, status="success",
                )
            finally:
                audit_db.close()

        channel = await asyncio.to_thread(ssh.open_interactive_shell, term="xterm-256color")
        await websocket.send_text("[已连接，输入命令后按 Enter 执行]\r\n")

        # 命令缓冲器：拦截用户输入，按回车拆分成命令写入审计日志
        cmd_buffer = []
        in_escape = False
        # 续行状态：上一个字符是反斜杠 \，下一个换行应被吞掉（bash 续行）
        line_continuation = False

        def _feed_input(text: str) -> list:
            """处理用户输入，返回本次产生的完整命令列表

            防绕过：
            - 反斜杠续行（\\ + 回车）：合并到下一行，不在换行时提交命令
            - Tab 补全：原样保留 \\t 字符到缓冲区（不再替换为 [TAB] 占位符），
              避免 r<TAB> 补全为 rm 后，缓冲区记录的 r[TAB] 无法匹配 rm 模式
            """
            nonlocal cmd_buffer, in_escape, line_continuation
            commands = []
            for ch in text:
                if in_escape:
                    # ESC 转义序列通常以字母结尾（如方向键 \x1b[A）
                    if ch.isalpha() or ch == '~':
                        in_escape = False
                    continue
                if ch == '\x1b':
                    in_escape = True
                    continue
                # 反斜杠续行处理：\ + \r/\n → 吞掉换行，合并到下一行
                if line_continuation and ch in ('\r', '\n'):
                    line_continuation = False
                    continue
                line_continuation = False
                if ch in ('\r', '\n'):
                    cmd = ''.join(cmd_buffer).strip()
                    if cmd:
                        commands.append(cmd)
                    cmd_buffer = []
                elif ch in ('\x7f', '\x08'):
                    if cmd_buffer:
                        cmd_buffer.pop()
                elif ch == '\x03':
                    cmd_buffer = []
                elif ch == '\x15':
                    cmd_buffer = []
                elif ch == '\\':
                    # 反斜杠：可能是续行，先标记，等下一个字符判断
                    line_continuation = True
                    cmd_buffer.append(ch)
                elif ch == '\t':
                    # Tab 原样保留到缓冲区，让 _normalize_command 合并多空格后参与匹配
                    cmd_buffer.append(' ')
                elif ord(ch) >= 32:
                    cmd_buffer.append(ch)
            return commands

        async def pump_browser_to_ssh():
            nonlocal cmd_buffer, in_escape, line_continuation
            while True:
                data = await websocket.receive_text()
                if channel.closed:
                    break
                # 会话期间实时校验 connect 权限 + 读取最新命令策略
                # 权限被撤销（用户停用 / 权限删除）时立即断开终端
                perm_db = SessionLocal()
                try:
                    if not has_remote_permission(perm_db, current_user.id, asset_id, "connect"):
                        try:
                            await websocket.send_text("\r\n[系统] 你的远程连接权限已被撤销，会话即将断开\r\n")
                        except Exception:
                            pass
                        try:
                            await websocket.close()
                        except Exception:
                            pass
                        return
                    live_policy = get_remote_command_policy(perm_db, current_user.id, asset_id)
                finally:
                    perm_db.close()
                if data.startswith("__resize__:"):
                    try:
                        _, cols, rows = data.split(":", 2)
                        await asyncio.to_thread(channel.resize_pty, width=int(cols), height=int(rows))
                    except Exception:
                        pass
                    continue
                # 先拦截记录命令，再转发到 SSH
                cmds = _feed_input(data)
                blocked = False
                for cmd in cmds:
                    sensitive_reason = _get_sensitive_command_reason(
                        cmd,
                        allow_sensitive_commands=live_policy["allow_sensitive_commands"],
                        allow_high_risk_commands=live_policy["allow_high_risk_commands"],
                        blocked_commands=live_policy["blocked_commands"],
                    )
                    if sensitive_reason:
                        blocked = True
                        audit_db = SessionLocal()
                        try:
                            write_audit_log(
                                audit_db, username=audit_username, action=ACTION_EXECUTE,
                                resource_type=RESOURCE_ASSET, resource_name=audit_hostname,
                                detail=f"终端拦截高危命令: {cmd}，原因: {sensitive_reason}",
                                ip_address=audit_ip, status="failure",
                            )
                        finally:
                            audit_db.close()
                        try:
                            await asyncio.to_thread(channel.send, "\x15")
                            await websocket.send_text(f"\r\n[已拦截] {sensitive_reason}: {cmd}\r\n")
                        except Exception:
                            pass
                        continue
                    audit_db = SessionLocal()
                    try:
                        write_audit_log(
                            audit_db, username=audit_username, action=ACTION_EXECUTE,
                            resource_type=RESOURCE_ASSET, resource_name=audit_hostname,
                            detail=f"终端执行: {cmd}",
                            ip_address=audit_ip, status="success",
                        )
                    finally:
                        audit_db.close()
                if blocked:
                    continue
                await asyncio.to_thread(channel.send, data)

        async def pump_ssh_to_browser():
            decoder = codecs.getincrementaldecoder("utf-8")("replace")
            while True:
                if channel.closed:
                    break
                if channel.recv_ready():
                    data = await asyncio.to_thread(channel.recv, 4096)
                    if not data:
                        break
                    text = _strip_terminal_noise(decoder.decode(data))
                    if text:
                        await websocket.send_text(text)
                else:
                    await asyncio.sleep(0.05)

        tasks = [
            asyncio.create_task(pump_browser_to_ssh()),
            asyncio.create_task(pump_ssh_to_browser()),
        ]
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        for task in done:
            if task.exception():
                raise task.exception()
    except WebSocketDisconnect:
        pass
    except Exception as e:
        try:
            await websocket.send_text(f"\r\n[终端错误] {e}\r\n")
        except Exception:
            pass
    finally:
        if channel:
            try:
                channel.close()
            except Exception:
                pass
        ssh.close()
        # 写入审计日志：终端会话结束
        if audit_username and audit_hostname:
            audit_db = SessionLocal()
            try:
                write_audit_log(
                    audit_db, username=audit_username, action=ACTION_TERMINAL,
                    resource_type=RESOURCE_ASSET, resource_name=audit_hostname,
                    detail="终端会话已断开",
                    ip_address=audit_ip, status="success",
                )
            finally:
                audit_db.close()


# ============================================================
# 分类相关接口
# ============================================================

@router.get("/categories/environments", response_model=List[dict])
def get_environment_categories(current_user: User = Depends(get_current_user)):
    """获取环境分类列表"""
    return [
        {"value": "prod", "label": "生产环境", "description": "生产环境服务器"},
        {"value": "test", "label": "测试环境", "description": "测试环境服务器"},
        {"value": "dev", "label": "开发环境", "description": "开发环境服务器"},
        {"value": "staging", "label": "预发布环境", "description": "预发布环境服务器"},
        {"value": "uat", "label": "UAT环境", "description": "用户验收测试环境"},
    ]


@router.get("/categories/roles", response_model=List[dict])
def get_role_categories(current_user: User = Depends(get_current_user)):
    """获取角色分类列表"""
    return [
        {"value": "web", "label": "Web服务器", "description": "Web前端服务器"},
        {"value": "app", "label": "应用服务器", "description": "应用服务器"},
        {"value": "db", "label": "数据库服务器", "description": "数据库服务器"},
        {"value": "cache", "label": "缓存服务器", "description": "缓存服务器(如Redis)"},
        {"value": "mq", "label": "消息队列服务器", "description": "消息队列服务器"},
        {"value": "storage", "label": "存储服务器", "description": "文件存储服务器"},
        {"value": "lb", "label": "负载均衡服务器", "description": "负载均衡服务器"},
        {"value": "monitor", "label": "监控服务器", "description": "监控服务器"},
        {"value": "ci", "label": "CI/CD服务器", "description": "持续集成/部署服务器"},
        {"value": "other", "label": "其他", "description": "其他类型服务器"},
    ]


@router.get("/categories/statuses", response_model=List[dict])
def get_status_categories(current_user: User = Depends(get_current_user)):
    """获取状态分类列表"""
    return [
        {"value": "online", "label": "在线", "color": "green"},
        {"value": "offline", "label": "离线", "color": "red"},
        {"value": "unknown", "label": "未知", "color": "gray"},
    ]
