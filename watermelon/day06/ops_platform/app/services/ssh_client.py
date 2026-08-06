"""
SSH连接服务 - 用于远程连接服务器并执行命令
支持密码、密钥内容、密钥文件路径三种认证方式
"""
import paramiko
import socket
import os
import sys
import json
import uuid
import time
import hashlib
import base64
import logging
import stat
import threading
from contextlib import contextmanager
from datetime import datetime
from typing import BinaryIO, Optional, Tuple, List, Dict, Any
from io import StringIO

logger = logging.getLogger(__name__)


# 主机密钥存储文件路径（首次连接时自动写入，后续校验一致性）
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_KNOWN_HOSTS_PATH = os.path.join(_PROJECT_ROOT, "known_hosts")
# 待确认的主机密钥变更记录（JSON 文件，持久化避免重启丢失）
_PENDING_HOST_KEYS_PATH = os.path.join(_PROJECT_ROOT, "pending_host_keys.json")

# 进程内序列化锁：asyncio.to_thread 会产生并发线程，需串行化对文件的访问
_known_hosts_lock = threading.Lock()


@contextmanager
def _lock_known_hosts():
    """序列化对 known_hosts / pending_host_keys.json 的并发访问。

    双重锁保护：
    - threading.Lock：应对 asyncio.to_thread 产生的同进程并发线程
    - 文件锁（O_EXCL 原子创建）：应对多进程部署（如 uvicorn --workers N）

    防止并发追加/删除导致的行号错位和文件损坏。
    """
    with _known_hosts_lock:
        lock_path = _KNOWN_HOSTS_PATH + '.lock'
        os.makedirs(os.path.dirname(lock_path) or '.', exist_ok=True)
        # 用 O_CREAT|O_EXCL 原子创建锁文件实现跨进程互斥
        while True:
            try:
                fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, f"{os.getpid()}".encode())
                os.close(fd)
                break
            except FileExistsError:
                # 陈旧锁检测：进程崩溃未释放时，超过 30 秒视为僵尸锁并接管
                try:
                    age = time.time() - os.path.getmtime(lock_path)
                    if age > 30:
                        os.remove(lock_path)
                        continue
                except OSError:
                    pass
                time.sleep(0.02)
        try:
            yield
        finally:
            try:
                os.remove(lock_path)
            except OSError:
                pass


class HostKeyChangedError(Exception):
    """主机密钥变更异常：已知主机返回了未知密钥，需管理员确认后才能追加。

    携带 hostname、新密钥指纹、已知密钥指纹列表，供前端展示。
    """

    def __init__(self, hostname: str, fingerprint: str, known_fingerprints: List[str]):
        self.hostname = hostname
        self.fingerprint = fingerprint
        self.known_fingerprints = known_fingerprints
        super().__init__(
            f"主机 {hostname} 的密钥已变更，可能遭遇中间人攻击或服务器已重装。\n"
            f"已知 {len(known_fingerprints)} 个密钥均不匹配，实际: {fingerprint}\n"
            f"请前往「主机密钥管理」确认新密钥后再连接。"
        )


def _format_host_key_fingerprint(host_key) -> str:
    """格式化主机密钥指纹，用于日志审计"""
    try:
        key_bytes = host_key.asbytes()
        digest = hashlib.sha256(key_bytes).digest()
        b64_digest = base64.b64encode(digest).decode().rstrip('=')
        return f"{host_key.get_name()} SHA256:{b64_digest}"
    except Exception:
        return str(host_key)


def _parse_known_hosts_for_host(hostname: str) -> List[Dict[str, Any]]:
    """读取 known_hosts 文件，返回该 hostname 下的所有密钥条目。

    自行解析文件而非依赖 paramiko.HostKeys.load()，因为后者对同 hostname+
    同 keytype 会覆盖（只保留最后一个），无法支持同 IP 多密钥共存场景。

    返回: [{"key_type": "ssh-ed25519", "key_b64": "...", "line_idx": N}, ...]
    """
    if not os.path.exists(_KNOWN_HOSTS_PATH):
        return []
    results = []
    try:
        with open(_KNOWN_HOSTS_PATH, 'r', encoding='utf-8') as f:
            for idx, line in enumerate(f):
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                parts = line.split()
                if len(parts) < 3:
                    continue
                host_field, key_type, key_b64 = parts[0], parts[1], parts[2]
                # known_hosts 的 host 字段可能是逗号分隔列表或哈希形式，这里只做精确匹配
                if hostname in host_field.split(','):
                    results.append({"key_type": key_type, "key_b64": key_b64, "line_idx": idx})
    except Exception as e:
        logger.error(f"读取 known_hosts 失败: {e}")
    return results


def _key_matches_any(hostname: str, key) -> Tuple[bool, List[str]]:
    """检查给定 key 是否匹配 known_hosts 中该 hostname 的任意一条记录。

    返回 (是否匹配, 已知密钥指纹列表)。
    """
    known_entries = _parse_known_hosts_for_host(hostname)
    if not known_entries:
        return False, []

    key_bytes = key.asbytes()
    key_b64 = base64.b64encode(key_bytes).decode()
    known_fps = []
    for entry in known_entries:
        # 重建指纹用于展示（与 _format_host_key_fingerprint 一致：SHA256 of raw key bytes）
        try:
            ktype = entry["key_type"]
            kb64 = entry["key_b64"]
            fp = f"{ktype} SHA256:" + base64.b64encode(
                hashlib.sha256(base64.b64decode(kb64)).digest()
            ).decode().rstrip('=')
            known_fps.append(fp)
        except Exception:
            known_fps.append(f"{entry['key_type']} {entry['key_b64'][:20]}...")

        if entry["key_type"] == key.get_name() and entry["key_b64"] == key_b64:
            return True, known_fps
    return False, known_fps


class _AuditHostKeyPolicy(paramiko.MissingHostKeyPolicy):
    """主机密钥策略（绕过 paramiko 内置检查，自行管理多密钥匹配）。

    设计要点：
    - 不使用 SSHClient.load_host_keys()，使 paramiko 始终走 missing_host_key 分支
    - 在 missing_host_key 中自行读 known_hosts 做多密钥匹配：
      * 无记录 → 首次连接，接受并保存
      * 匹配任一已知密钥 → 放行（支持同 IP 多密钥共存）
      * 都不匹配 → 记录待确认项并抛 HostKeyChangedError，由管理员在前端确认
    - 若设置了 expected_host_key（资产预绑定的指纹），则严格匹配，不走 TOFU

    这样可规避 paramiko 在 host key 不匹配时直接抛 BadHostKeyException 绕过 policy
    的问题，使多密钥匹配逻辑真正生效。
    """

    def __init__(self, expected_host_key: str = None):
        self.expected_host_key = expected_host_key.strip() if expected_host_key else None

    def missing_host_key(self, client, hostname, key):
        """主机密钥处理入口（paramiko 认为未知主机时调用）"""
        fingerprint = _format_host_key_fingerprint(key)

        # 若资产预绑定了预期指纹，严格匹配，不走 TOFU 也不走多密钥放行
        if self.expected_host_key:
            # 标准化：去掉空格，统一大写比较
            expected = self.expected_host_key.replace(" ", "").upper()
            actual = fingerprint.replace(" ", "").upper()
            if expected == actual:
                client.get_host_keys().add(hostname, key.get_name(), key)
                return
            logger.error(
                f"🚨 主机密钥与资产预绑定指纹不匹配！hostname={hostname}\n"
                f"  预期: {self.expected_host_key}\n"
                f"  实际: {fingerprint}"
            )
            raise paramiko.SSHException(
                f"主机 {hostname} 的密钥与资产预绑定的预期指纹不匹配。\n"
                f"实际: {fingerprint}\n"
                f"如确认服务器变更，请在资产编辑界面更新「预期主机密钥指纹」。"
            )

        # 整个匹配+写入流程加锁，保证读取 known_hosts 与写入/记录待确认的原子性
        with _lock_known_hosts():
            # 自行读 known_hosts 校验多密钥匹配
            matched, known_fps = _key_matches_any(hostname, key)

            if matched:
                # 匹配已知密钥中的某一条 → 放行（不重复写入）
                client.get_host_keys().add(hostname, key.get_name(), key)
                return

            if not known_fps:
                # 完全未知的主机 → 首次连接，自动接受并保存
                logger.warning(
                    f"⚠️  首次连接主机 {hostname}，自动接受主机密钥: {fingerprint}\n"
                    f"请核实该指纹是否为目标服务器的真实密钥。"
                )
                try:
                    self._save_to_known_hosts(hostname, key)
                except Exception as e:
                    logger.error(f"写入 known_hosts 文件失败: {e}")
                client.get_host_keys().add(hostname, key.get_name(), key)
                return

            # 已知主机但密钥都不匹配 → 记录待确认项，拒绝连接
            logger.error(
                f"🚨 主机密钥不匹配！hostname={hostname}\n"
                f"  已知密钥（共 {len(known_fps)} 个）:\n"
                + "".join(f"    - {fp}\n" for fp in known_fps)
                + f"  实际: {fingerprint}\n"
                f"  已记录为待确认项，需管理员在「主机密钥管理」中确认后追加。"
            )
            _add_pending_host_key(hostname, key, fingerprint, known_fps)
        raise HostKeyChangedError(hostname, fingerprint, known_fps)

    def _save_to_known_hosts(self, hostname, key):
        """保存主机密钥到 known_hosts 文件（追加，保留同 IP 的其他密钥）"""
        key_bytes = key.asbytes()
        key_b64 = base64.b64encode(key_bytes).decode()

        os.makedirs(os.path.dirname(_KNOWN_HOSTS_PATH) or '.', exist_ok=True)

        existing = set()
        if os.path.exists(_KNOWN_HOSTS_PATH):
            with open(_KNOWN_HOSTS_PATH, 'r', encoding='utf-8') as f:
                existing = set(line.strip() for line in f)

        entry = f"{hostname} {key.get_name()} {key_b64}"
        if entry not in existing:
            with open(_KNOWN_HOSTS_PATH, 'a', encoding='utf-8') as f:
                f.write(entry + '\n')
            logger.info(f"主机密钥已保存到 known_hosts: {hostname}")


# ============================================================
# 待确认主机密钥变更管理（供 routes/host_keys.py 复用）
# ============================================================

def _load_pending() -> List[Dict[str, Any]]:
    """读取待确认密钥变更列表"""
    if not os.path.exists(_PENDING_HOST_KEYS_PATH):
        return []
    try:
        with open(_PENDING_HOST_KEYS_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception as e:
        logger.error(f"读取 pending_host_keys.json 失败: {e}")
        return []


def _save_pending(items: List[Dict[str, Any]]) -> None:
    """写入待确认密钥变更列表"""
    os.makedirs(os.path.dirname(_PENDING_HOST_KEYS_PATH) or '.', exist_ok=True)
    tmp = _PENDING_HOST_KEYS_PATH + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(items, f, ensure_ascii=False, indent=2)
    os.replace(tmp, _PENDING_HOST_KEYS_PATH)


def _add_pending_host_key(hostname: str, key, fingerprint: str, known_fingerprints: List[str]) -> str:
    """记录一条待确认的密钥变更，返回其 id。

    若同 hostname + 同 key_b64 已存在则更新时间戳，避免重复堆积。
    """
    key_b64 = base64.b64encode(key.asbytes()).decode()
    items = _load_pending()
    # 去重：同 hostname + 同 key_b64 只保留一条（刷新已知指纹与时间）
    items = [i for i in items if not (i.get("hostname") == hostname and i.get("key_b64") == key_b64)]
    item_id = str(uuid.uuid4())
    items.append({
        "id": item_id,
        "hostname": hostname,
        "key_type": key.get_name(),
        "key_b64": key_b64,
        "fingerprint": fingerprint,
        "known_fingerprints": known_fingerprints,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    })
    _save_pending(items)
    return item_id


def list_pending_host_keys() -> List[Dict[str, Any]]:
    """返回所有待确认的密钥变更（按时间倒序）"""
    items = _load_pending()
    items.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
    return items


def confirm_pending_host_key(item_id: str, fingerprint_suffix: str = None) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
    """确认一条待确认项：将新密钥追加到 known_hosts，并从待确认列表删除。

    Args:
        item_id: 待确认项 ID
        fingerprint_suffix: 二次校验 - 要求管理员输入新密钥指纹的后 8 位字符，
                           防止误操作或非授权人员直接点击确认。传 None 则跳过校验（不推荐）。
    Returns:
        (是否成功, 消息, 待确认项详情) - 详情供调用方写审计日志
    """
    with _lock_known_hosts():
        items = _load_pending()
        target = next((i for i in items if i.get("id") == item_id), None)
        if not target:
            return False, "待确认项不存在或已被处理", None
        # 二次校验：指纹后 8 位（去掉 SHA256: 前缀和可能的等号）
        if fingerprint_suffix is not None:
            fp_clean = target.get("fingerprint", "").replace("SHA256:", "").rstrip("=")
            expected_suffix = fp_clean[-8:] if len(fp_clean) >= 8 else fp_clean
            if fingerprint_suffix.strip() != expected_suffix:
                return False, f"指纹校验失败：请输入新密钥指纹的最后 8 位字符（{expected_suffix[:4]}...）", None
        # 追加到 known_hosts（保留同 IP 其他密钥）
        entry = f"{target['hostname']} {target['key_type']} {target['key_b64']}"
        os.makedirs(os.path.dirname(_KNOWN_HOSTS_PATH) or '.', exist_ok=True)
        existing = set()
        if os.path.exists(_KNOWN_HOSTS_PATH):
            with open(_KNOWN_HOSTS_PATH, 'r', encoding='utf-8') as f:
                existing = set(line.strip() for line in f)
        if entry not in existing:
            with open(_KNOWN_HOSTS_PATH, 'a', encoding='utf-8') as f:
                f.write(entry + '\n')
        # 从待确认列表移除
        items = [i for i in items if i.get("id") != item_id]
        _save_pending(items)
    logger.warning(f"管理员已确认主机密钥变更: {target['hostname']} -> {target['fingerprint']}")
    return True, f"已确认并追加 {target['hostname']} 的新密钥", target


def reject_pending_host_key(item_id: str) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
    """拒绝一条待确认项：直接从待确认列表删除（不写入 known_hosts）。

    Returns:
        (是否成功, 消息, 待确认项详情) - 详情供调用方写审计日志
    """
    with _lock_known_hosts():
        items = _load_pending()
        target = next((i for i in items if i.get("id") == item_id), None)
        if not target:
            return False, "待确认项不存在或已被处理", None
        items = [i for i in items if i.get("id") != item_id]
        _save_pending(items)
    logger.warning(f"管理员已拒绝主机密钥变更: {target['hostname']} -> {target['fingerprint']}")
    return True, f"已拒绝 {target['hostname']} 的密钥变更", target


def list_known_host_keys() -> List[Dict[str, Any]]:
    """返回 known_hosts 中所有条目（按 hostname 分组展开）"""
    with _lock_known_hosts():
        if not os.path.exists(_KNOWN_HOSTS_PATH):
            return []
        results = []
        try:
            with open(_KNOWN_HOSTS_PATH, 'r', encoding='utf-8') as f:
                for idx, line in enumerate(f):
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue
                    parts = line.split()
                    if len(parts) < 3:
                        continue
                    host_field, key_type, key_b64 = parts[0], parts[1], parts[2]
                    try:
                        fp = f"{key_type} SHA256:" + base64.b64encode(
                            hashlib.sha256(base64.b64decode(key_b64)).digest()
                        ).decode().rstrip('=')
                    except Exception:
                        fp = f"{key_type} {key_b64[:20]}..."
                    results.append({
                        "id": f"known-{idx}",
                        "hostname": host_field,
                        "key_type": key_type,
                        "fingerprint": fp,
                        "line_idx": idx,
                    })
        except Exception as e:
            logger.error(f"读取 known_hosts 失败: {e}")
        return results


def delete_known_host_key(line_idx: int) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
    """按行号删除 known_hosts 中的一条密钥记录。

    Returns:
        (是否成功, 消息, 被删除条目详情) - 详情供调用方写审计日志
    """
    with _lock_known_hosts():
        if not os.path.exists(_KNOWN_HOSTS_PATH):
            return False, "known_hosts 文件不存在", None
        with open(_KNOWN_HOSTS_PATH, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        if line_idx < 0 or line_idx >= len(lines):
            return False, "行号超出范围", None
        removed_line = lines[line_idx].strip()
        parts = removed_line.split()
        deleted_detail = {"hostname": parts[0] if parts else "", "fingerprint": removed_line}
        del lines[line_idx]
        with open(_KNOWN_HOSTS_PATH, 'w', encoding='utf-8') as f:
            f.writelines(lines)
    logger.warning(f"已删除 known_hosts 记录: {removed_line}")
    return True, f"已删除: {removed_line}", deleted_detail


def _get_host_key_policy(expected_host_key: str = None):
    """获取主机密钥策略实例。

    Args:
        expected_host_key: 资产预绑定的预期指纹。若设置，则严格匹配，不走 TOFU。
    """
    return _AuditHostKeyPolicy(expected_host_key=expected_host_key)


def _get_private_key_classes():
    """获取当前 Paramiko 版本实际支持的私钥类型"""
    key_class_names = ["RSAKey", "Ed25519Key", "ECDSAKey", "DSSKey"]
    return [
        getattr(paramiko, class_name)
        for class_name in key_class_names
        if hasattr(paramiko, class_name)
    ]


def _load_private_key(key_data: str):
    """尝试用多种格式加载私钥"""
    for key_class in _get_private_key_classes():
        try:
            return key_class.from_private_key(StringIO(key_data))
        except (paramiko.SSHException, ValueError, TypeError):
            continue
    return None


def _load_certificate(private_key, ssh_cert: str = None, ssh_cert_path: str = None) -> Tuple[bool, str]:
    """把 OpenSSH 用户证书加载到私钥上"""
    cert_value = ssh_cert_path or ssh_cert
    if not cert_value:
        return True, "未配置证书"
    if ssh_cert_path and not os.path.isfile(ssh_cert_path):
        return False, f"证书文件不存在: {ssh_cert_path}"
    try:
        private_key.load_certificate(cert_value)
        return True, "证书加载成功"
    except Exception as e:
        return False, f"无法加载SSH证书: {e}"


class SSHClient:
    """SSH客户端封装类，支持密码/密钥内容/密钥文件路径"""

    def __init__(self, timeout: int = 10):
        self.timeout = timeout
        self.client: Optional[paramiko.SSHClient] = None
        self.jump_client: Optional[paramiko.SSHClient] = None

    def _connect_client(
        self,
        client: paramiko.SSHClient,
        host: str,
        port: int,
        username: str,
        password: str = None,
        ssh_key: str = None,
        ssh_key_path: str = None,
        ssh_cert: str = None,
        ssh_cert_path: str = None,
        timeout: int = None,
        sock=None
    ) -> Tuple[bool, str]:
        """使用密码、密钥内容或密钥文件连接 SSH 客户端"""
        timeout = timeout or self.timeout

        # 经跳板机连接时，known_hosts 用虚拟标识区分，避免与直连同 IP 主机互相覆盖
        # 例如 192.168.8.11 直连保存为 "192.168.8.11"，经跳板机保存为 "via-jump@192.168.8.11:22"
        # 两类场景的密钥互不干扰，跳板机透传的目标服务器密钥不再被直连密钥覆盖
        known_host_key = host
        if sock is not None:
            known_host_key = f"via-jump@{host}:{port}"

        if ssh_key_path:
            if not os.path.isfile(ssh_key_path):
                return False, f"密钥文件不存在: {ssh_key_path}"
            private_key = None
            for key_class in _get_private_key_classes():
                try:
                    private_key = key_class.from_private_key_file(ssh_key_path)
                    break
                except (paramiko.SSHException, ValueError, TypeError):
                    continue
            if not private_key:
                return False, f"无法加载密钥文件: {ssh_key_path}，请确认格式(RSA/Ed25519/ECDSA)或密钥是否加密"
            cert_ok, cert_msg = _load_certificate(private_key, ssh_cert, ssh_cert_path)
            if not cert_ok:
                return False, cert_msg
            client.connect(
                hostname=known_host_key,
                port=port,
                username=username,
                pkey=private_key,
                timeout=timeout,
                allow_agent=False,
                look_for_keys=False,
                sock=sock
            )
            return True, "连接成功"

        if ssh_key:
            private_key = _load_private_key(ssh_key)
            if not private_key:
                return False, "无法解析SSH密钥内容，请确认格式(RSA/Ed25519/ECDSA)"
            cert_ok, cert_msg = _load_certificate(private_key, ssh_cert, ssh_cert_path)
            if not cert_ok:
                return False, cert_msg
            client.connect(
                hostname=known_host_key,
                port=port,
                username=username,
                pkey=private_key,
                timeout=timeout,
                allow_agent=False,
                look_for_keys=False,
                sock=sock
            )
            return True, "连接成功"

        if password:
            client.connect(
                hostname=known_host_key,
                port=port,
                username=username,
                password=password,
                timeout=timeout,
                allow_agent=False,
                look_for_keys=False,
                sock=sock
            )
            return True, "连接成功"

        return False, "未提供密码、SSH密钥或密钥文件路径"

    def connect(
        self,
        host: str,
        port: int = 22,
        username: str = None,
        password: str = None,
        ssh_key: str = None,
        ssh_key_path: str = None,
        ssh_cert: str = None,
        ssh_cert_path: str = None,
        sudo_password: str = None,
        sudo_enabled: bool = False,
        jump_enabled: bool = False,
        jump_host: str = None,
        jump_port: int = 22,
        jump_username: str = None,
        jump_password: str = None,
        jump_ssh_key: str = None,
        jump_ssh_key_path: str = None,
        jump_ssh_cert: str = None,
        jump_ssh_cert_path: str = None,
        timeout: int = None,
        expected_host_key: str = None
    ) -> Tuple[bool, str]:
        """
        连接到远程服务器

        支持三种认证方式（优先级从高到低）：
        1. ssh_key_path - 服务器上的私钥文件路径
        2. ssh_key      - 私钥内容字符串
        3. password     - 密码

        Args:
            host: 主机IP
            port: SSH端口
            username: 用户名
            password: 密码
            ssh_key: SSH私钥内容
            ssh_key_path: SSH私钥文件路径
            timeout: 超时时间

        Returns:
            (是否成功, 错误信息)
        """
        timeout = timeout or self.timeout

        try:
            sock = None
            if jump_enabled:
                if not jump_host or not jump_username:
                    return False, "已启用跳板机，但未填写跳板机地址或用户名"
                if not jump_password and not jump_ssh_key and not jump_ssh_key_path:
                    return False, "已启用跳板机，但未配置跳板机密码、SSH密钥或密钥文件路径"

                self.jump_client = paramiko.SSHClient()
                self.jump_client.set_missing_host_key_policy(_get_host_key_policy())
                # 不调用 load_host_keys()：让 paramiko 走 missing_host_key 分支，
                # 由 _AuditHostKeyPolicy 自行读 known_hosts 做多密钥匹配，
                # 避免 paramiko 在密钥不匹配时直接抛 BadHostKeyException 绕过 policy
                jump_ok, jump_msg = self._connect_client(
                    client=self.jump_client,
                    host=jump_host,
                    port=jump_port or 22,
                    username=jump_username,
                    password=jump_password,
                    ssh_key=jump_ssh_key,
                    ssh_key_path=jump_ssh_key_path,
                    ssh_cert=jump_ssh_cert,
                    ssh_cert_path=jump_ssh_cert_path,
                    timeout=timeout
                )
                if not jump_ok:
                    return False, f"跳板机连接失败: {jump_msg}"

                transport = self.jump_client.get_transport()
                if not transport or not transport.is_active():
                    return False, "跳板机连接不可用"
                sock = transport.open_channel(
                    "direct-tcpip",
                    (host, port),
                    ("127.0.0.1", 0)
                )

            self.client = paramiko.SSHClient()
            self.client.set_missing_host_key_policy(_get_host_key_policy(expected_host_key=expected_host_key))
            # 不调用 load_host_keys()：同上，由 policy 自行管理多密钥匹配
            success, message = self._connect_client(
                client=self.client,
                host=host,
                port=port,
                username=username,
                password=password,
                ssh_key=ssh_key,
                ssh_key_path=ssh_key_path,
                ssh_cert=ssh_cert,
                ssh_cert_path=ssh_cert_path,
                timeout=timeout,
                sock=sock
            )
            if not success:
                return False, message

            via_jump = f"，经跳板机 {jump_host}:{jump_port or 22}" if jump_enabled else ""
            logger.info(f"成功连接到服务器 {host}:{port}{via_jump}")
            return True, f"连接成功{via_jump}"

        except HostKeyChangedError as e:
            # 主机密钥变更：已记录为待确认项，引导管理员前往「主机密钥管理」确认
            logger.warning(f"主机密钥变更待确认: {host}:{port} - {e.hostname}")
            error_msg = (
                f"主机密钥已变更，连接已被拒绝。\n"
                f"实际指纹: {e.fingerprint}\n"
                f"请前往「主机密钥管理」确认新密钥后重新连接。"
            )
            return False, error_msg

        except paramiko.AuthenticationException:
            error_msg = "认证失败：用户名或密码/密钥错误"
            logger.error(f"SSH认证失败: {host}:{port}")
            return False, error_msg

        except paramiko.SSHException as e:
            error_msg = f"SSH连接错误: {str(e)}"
            logger.error(f"SSH连接错误: {host}:{port} - {e}")
            return False, error_msg

        except socket.timeout:
            error_msg = f"连接超时：无法在{timeout}秒内连接到服务器"
            logger.error(f"SSH连接超时: {host}:{port}")
            return False, error_msg

        except socket.error as e:
            error_msg = f"网络错误: {str(e)}"
            logger.error(f"网络错误: {host}:{port} - {e}")
            return False, error_msg

        except Exception as e:
            error_msg = f"未知错误: {str(e)}"
            logger.error(f"未知错误: {host}:{port} - {e}")
            return False, error_msg

    def execute_command(self, command: str, timeout: int = 30, stdin_data: str = None) -> Tuple[bool, dict]:
        """
        执行远程命令

        Args:
            command: 要执行的命令
            timeout: 命令执行超时时间
            stdin_data: 通过 stdin 写入的数据（用于 sudo -S 安全传递密码，
                        避免在命令行/进程列表中暴露）

        Returns:
            (是否成功, {'stdout': 标准输出, 'stderr': 标准错误, 'exit_code': 退出码})
        """
        if not self.client:
            return False, {"error": "未建立SSH连接"}

        try:
            # 使用 get_pty=False 让 stdin/stdout/stderr 分离，便于精确控制
            stdin, stdout, stderr = self.client.exec_command(command, timeout=timeout, get_pty=False)

            # 如果有 stdin 数据（如 sudo 密码），通过 channel 写入后关闭
            if stdin_data is not None:
                try:
                    stdin.write(stdin_data)
                    stdin.write('\n')
                    stdin.flush()
                except Exception:
                    pass
                finally:
                    try:
                        stdin.channel.shutdown_write()
                    except Exception:
                        pass

            exit_code = stdout.channel.recv_exit_status()
            stdout_text = stdout.read().decode('utf-8', errors='ignore')
            stderr_text = stderr.read().decode('utf-8', errors='ignore')

            result = {
                'stdout': stdout_text,
                'stderr': stderr_text,
                'exit_code': exit_code
            }

            # 日志脱敏：含 sudo -S 的命令不记录完整内容
            log_cmd = command if 'sudo -S' not in command else '[sudo -S 命令，已脱敏]'
            logger.info(f"命令执行成功: {log_cmd}")
            return True, result

        except paramiko.SSHException as e:
            logger.error(f"命令执行失败: {command} - {e}")
            return False, {'error': str(e)}

        except socket.timeout:
            logger.error(f"命令执行超时: {command}")
            return False, {'error': f'命令执行超时({timeout}秒)'}

        except Exception as e:
            logger.error(f"命令执行异常: {command} - {e}")
            return False, {'error': str(e)}

    def open_interactive_shell(self, term: str = "xterm", width: int = 120, height: int = 32):
        """打开交互式 SSH Shell 通道"""
        if not self.client:
            raise RuntimeError("未建立SSH连接")
        return self.client.invoke_shell(term=term, width=width, height=height)

    def upload_file(self, local_file: BinaryIO, remote_path: str) -> Tuple[bool, str]:
        """通过 SFTP 上传文件到远程服务器"""
        if not self.client:
            return False, "未建立SSH连接"
        sftp = None
        try:
            sftp = self.client.open_sftp()
            with sftp.open(remote_path, "wb") as remote_file:
                while True:
                    chunk = local_file.read(1024 * 1024)
                    if not chunk:
                        break
                    remote_file.write(chunk)
            return True, "上传成功"
        except Exception as e:
            logger.error(f"SFTP上传失败: {remote_path} - {e}")
            return False, f"上传失败: {e}"
        finally:
            if sftp:
                sftp.close()

    def download_file(self, remote_path: str) -> Tuple[bool, dict]:
        """通过 SFTP 从远程服务器下载文件"""
        if not self.client:
            return False, {"error": "未建立SSH连接"}
        sftp = None
        try:
            sftp = self.client.open_sftp()
            with sftp.open(remote_path, "rb") as remote_file:
                data = remote_file.read()
            return True, {
                "filename": os.path.basename(remote_path.rstrip("/")) or "download.bin",
                "data": data
            }
        except Exception as e:
            logger.error(f"SFTP下载失败: {remote_path} - {e}")
            return False, {"error": f"下载失败: {e}"}
        finally:
            if sftp:
                sftp.close()

    def list_dir(self, remote_path: str = ".") -> Tuple[bool, dict]:
        """通过 SFTP 列出远程目录内容"""
        if not self.client:
            return False, {"error": "未建立SSH连接"}
        sftp = None
        try:
            sftp = self.client.open_sftp()
            path = remote_path.strip() or "."
            if path == "~":
                path = "."
            abs_path = sftp.normalize(path)
            entries = sftp.listdir_attr(abs_path)
            result = []
            for attr in entries:
                is_dir = stat.S_ISDIR(attr.st_mode)
                result.append({
                    "name": attr.filename,
                    "type": "directory" if is_dir else "file",
                    "size": attr.st_size or 0,
                    "mtime": attr.st_mtime or 0,
                })
            result.sort(key=lambda x: (0 if x["type"] == "directory" else 1, x["name"].lower()))
            return True, {"path": abs_path, "entries": result}
        except Exception as e:
            logger.error(f"SFTP列出目录失败: {remote_path} - {e}")
            return False, {"error": f"列出目录失败: {e}"}
        finally:
            if sftp:
                sftp.close()

    def check_connection(self) -> bool:
        """检查SSH连接是否仍然有效"""
        if not self.client:
            return False
        try:
            transport = self.client.get_transport()
            return transport is not None and transport.is_active()
        except:
            return False

    def close(self):
        """关闭SSH连接"""
        if self.client:
            try:
                self.client.close()
                logger.info("SSH连接已关闭")
            except:
                pass
            finally:
                self.client = None
        if self.jump_client:
            try:
                self.jump_client.close()
                logger.info("跳板机SSH连接已关闭")
            except:
                pass
            finally:
                self.jump_client = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


def test_server_connection(
    host: str,
    port: int = 22,
    username: str = None,
    password: str = None,
    ssh_key: str = None,
    ssh_key_path: str = None,
    ssh_cert: str = None,
    ssh_cert_path: str = None,
    sudo_password: str = None,
    sudo_enabled: bool = False,
    jump_enabled: bool = False,
    jump_host: str = None,
    jump_port: int = 22,
    jump_username: str = None,
    jump_password: str = None,
    jump_ssh_key: str = None,
    jump_ssh_key_path: str = None,
    jump_ssh_cert: str = None,
    jump_ssh_cert_path: str = None,
    timeout: int = 10
) -> Tuple[bool, str]:
    """
    测试服务器连接

    Returns:
        (是否成功, 消息)
    """
    with SSHClient(timeout=timeout) as ssh:
        success, message = ssh.connect(
            host=host,
            port=port,
            username=username,
            password=password,
            ssh_key=ssh_key,
            ssh_key_path=ssh_key_path,
            ssh_cert=ssh_cert,
            ssh_cert_path=ssh_cert_path,
            jump_enabled=jump_enabled,
            jump_host=jump_host,
            jump_port=jump_port,
            jump_username=jump_username,
            jump_password=jump_password,
            jump_ssh_key=jump_ssh_key,
            jump_ssh_key_path=jump_ssh_key_path,
            jump_ssh_cert=jump_ssh_cert,
            jump_ssh_cert_path=jump_ssh_cert_path,
            timeout=timeout
        )
        return success, message


def execute_remote_command(
    host: str,
    port: int,
    username: str,
    password: str = None,
    ssh_key: str = None,
    ssh_key_path: str = None,
    ssh_cert: str = None,
    ssh_cert_path: str = None,
    sudo_password: str = None,
    sudo_enabled: bool = False,
    jump_enabled: bool = False,
    jump_host: str = None,
    jump_port: int = 22,
    jump_username: str = None,
    jump_password: str = None,
    jump_ssh_key: str = None,
    jump_ssh_key_path: str = None,
    jump_ssh_cert: str = None,
    jump_ssh_cert_path: str = None,
    command: str = None,
    connect_timeout: int = 10,
    command_timeout: int = 30,
    stdin_data: str = None
) -> Tuple[bool, dict]:
    """
    执行远程命令

    Args:
        stdin_data: 可选，通过 SSH stdin channel 写入的数据（用于 sudo -S 安全传递密码）

    Returns:
        (是否成功, 结果字典)
    """
    with SSHClient(timeout=connect_timeout) as ssh:
        success, message = ssh.connect(
            host=host,
            port=port,
            username=username,
            password=password,
            ssh_key=ssh_key,
            ssh_key_path=ssh_key_path,
            ssh_cert=ssh_cert,
            ssh_cert_path=ssh_cert_path,
            jump_enabled=jump_enabled,
            jump_host=jump_host,
            jump_port=jump_port,
            jump_username=jump_username,
            jump_password=jump_password,
            jump_ssh_key=jump_ssh_key,
            jump_ssh_key_path=jump_ssh_key_path,
            jump_ssh_cert=jump_ssh_cert,
            jump_ssh_cert_path=jump_ssh_cert_path
        )

        if not success:
            return False, {'error': message}

        if not command:
            return True, {'message': '连接成功，但未执行命令'}

        return ssh.execute_command(command, timeout=command_timeout, stdin_data=stdin_data)


def upload_remote_file(
    file_obj: BinaryIO,
    remote_path: str,
    host: str,
    port: int,
    username: str,
    password: str = None,
    ssh_key: str = None,
    ssh_key_path: str = None,
    ssh_cert: str = None,
    ssh_cert_path: str = None,
    sudo_password: str = None,
    sudo_enabled: bool = False,
    jump_enabled: bool = False,
    jump_host: str = None,
    jump_port: int = 22,
    jump_username: str = None,
    jump_password: str = None,
    jump_ssh_key: str = None,
    jump_ssh_key_path: str = None,
    jump_ssh_cert: str = None,
    jump_ssh_cert_path: str = None,
    timeout: int = 10
) -> Tuple[bool, str]:
    """上传文件到远程服务器"""
    with SSHClient(timeout=timeout) as ssh:
        success, message = ssh.connect(
            host=host,
            port=port,
            username=username,
            password=password,
            ssh_key=ssh_key,
            ssh_key_path=ssh_key_path,
            ssh_cert=ssh_cert,
            ssh_cert_path=ssh_cert_path,
            jump_enabled=jump_enabled,
            jump_host=jump_host,
            jump_port=jump_port,
            jump_username=jump_username,
            jump_password=jump_password,
            jump_ssh_key=jump_ssh_key,
            jump_ssh_key_path=jump_ssh_key_path,
            jump_ssh_cert=jump_ssh_cert,
            jump_ssh_cert_path=jump_ssh_cert_path,
            timeout=timeout
        )
        if not success:
            return False, message
        return ssh.upload_file(file_obj, remote_path)


def download_remote_file(
    remote_path: str,
    host: str,
    port: int,
    username: str,
    password: str = None,
    ssh_key: str = None,
    ssh_key_path: str = None,
    ssh_cert: str = None,
    ssh_cert_path: str = None,
    sudo_password: str = None,
    sudo_enabled: bool = False,
    jump_enabled: bool = False,
    jump_host: str = None,
    jump_port: int = 22,
    jump_username: str = None,
    jump_password: str = None,
    jump_ssh_key: str = None,
    jump_ssh_key_path: str = None,
    jump_ssh_cert: str = None,
    jump_ssh_cert_path: str = None,
    timeout: int = 10
) -> Tuple[bool, dict]:
    """从远程服务器下载文件"""
    with SSHClient(timeout=timeout) as ssh:
        success, message = ssh.connect(
            host=host,
            port=port,
            username=username,
            password=password,
            ssh_key=ssh_key,
            ssh_key_path=ssh_key_path,
            ssh_cert=ssh_cert,
            ssh_cert_path=ssh_cert_path,
            jump_enabled=jump_enabled,
            jump_host=jump_host,
            jump_port=jump_port,
            jump_username=jump_username,
            jump_password=jump_password,
            jump_ssh_key=jump_ssh_key,
            jump_ssh_key_path=jump_ssh_key_path,
            jump_ssh_cert=jump_ssh_cert,
            jump_ssh_cert_path=jump_ssh_cert_path,
            timeout=timeout
        )
        if not success:
            return False, {"error": message}
        return ssh.download_file(remote_path)


def list_remote_dir(
    remote_path: str = ".",
    host: str = None,
    port: int = 22,
    username: str = None,
    password: str = None,
    ssh_key: str = None,
    ssh_key_path: str = None,
    ssh_cert: str = None,
    ssh_cert_path: str = None,
    sudo_password: str = None,
    sudo_enabled: bool = False,
    jump_enabled: bool = False,
    jump_host: str = None,
    jump_port: int = 22,
    jump_username: str = None,
    jump_password: str = None,
    jump_ssh_key: str = None,
    jump_ssh_key_path: str = None,
    jump_ssh_cert: str = None,
    jump_ssh_cert_path: str = None,
    timeout: int = 10
) -> Tuple[bool, dict]:
    """列出远程服务器目录内容"""
    with SSHClient(timeout=timeout) as ssh:
        success, message = ssh.connect(
            host=host,
            port=port,
            username=username,
            password=password,
            ssh_key=ssh_key,
            ssh_key_path=ssh_key_path,
            ssh_cert=ssh_cert,
            ssh_cert_path=ssh_cert_path,
            jump_enabled=jump_enabled,
            jump_host=jump_host,
            jump_port=jump_port,
            jump_username=jump_username,
            jump_password=jump_password,
            jump_ssh_key=jump_ssh_key,
            jump_ssh_key_path=jump_ssh_key_path,
            jump_ssh_cert=jump_ssh_cert,
            jump_ssh_cert_path=jump_ssh_cert_path,
            timeout=timeout
        )
        if not success:
            return False, {"error": message}
        return ssh.list_dir(remote_path)
