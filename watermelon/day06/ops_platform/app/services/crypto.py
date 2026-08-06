"""
敏感字段加密/解密服务 - 使用 Fernet 对称加密保护 SSH 密码、私钥、证书等

User passwords 使用 PBKDF2 单向哈希（不可逆），在 security.py 中处理。
Asset SSH passwords / private keys / certificates 使用 Fernet 对称加密（可逆），由本模块处理。
"""
import base64
import hashlib
import logging
from cryptography.fernet import Fernet
from app.config import settings

logger = logging.getLogger(__name__)

# 从 SECRET_KEY 派生 32 字节 Fernet 密钥
_fernet = None


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        key_material = hashlib.sha256(settings.SECRET_KEY.encode()).digest()
        fernet_key = base64.urlsafe_b64encode(key_material)
        _fernet = Fernet(fernet_key)
    return _fernet


def encrypt_password(plaintext: str) -> str:
    """加密明文密码，返回 Base64 密文字符串"""
    return encrypt_secret(plaintext)


def decrypt_password(ciphertext: str) -> str:
    """解密密文密码，返回明文字符串（保留旧函数名以向后兼容）"""
    return decrypt_secret(ciphertext, "密码")


def encrypt_secret(plaintext: str) -> str:
    """加密任意敏感字符串（密码 / 私钥 / 证书），返回 Base64 密文"""
    if not plaintext:
        return plaintext
    try:
        return _get_fernet().encrypt(plaintext.encode()).decode()
    except Exception as e:
        logger.error(f"敏感字段加密失败: {e}")
        raise


def decrypt_secret(ciphertext: str, field_name: str = "敏感字段") -> str:
    """解密敏感字符串。

    解密失败时抛 ValueError，不再回退明文，避免：
    - SECRET_KEY 变更后用错误密钥"解密"得到乱码当真实值用
    - 数据库被篡改后把密文当明文使用导致连接失败难以排查
    调用方应在使用前捕获并返回友好错误。

    field_name: 用于错误日志和异常信息中标识字段类型（密码/私钥/证书）
    """
    if not ciphertext:
        return ciphertext
    # 非密文格式（旧明文）也按异常处理，由 main.py 的迁移逻辑统一加密后再访问
    if not is_encrypted(ciphertext):
        raise ValueError(f"{field_name}字段不是合法的密文格式，请重新保存")
    try:
        return _get_fernet().decrypt(ciphertext.encode()).decode()
    except Exception as e:
        logger.error(f"{field_name}解密失败：可能是 SECRET_KEY 变更或数据损坏")
        raise ValueError(f"{field_name}解密失败：数据损坏或密钥不匹配") from e


def is_encrypted(value: str) -> bool:
    """判断字符串是否已经是 Fernet 密文格式"""
    if not value:
        return False
    return value.startswith("gAAAAA")