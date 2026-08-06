"""
传输层敏感字段加密：前端使用 RSA-OAEP 公钥加密，后端使用持久化私钥解密。
注意：生产环境仍建议配合 HTTPS，本模块用于避免密码在请求体中以明文出现。
"""
import base64
import logging
import os

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

logger = logging.getLogger(__name__)

# RSA 私钥持久化路径：与数据库文件同目录，通过 Docker volume 持久化
# 容器重启后密钥对不变，浏览器缓存的公钥仍然有效，避免每次重启都要求用户清缓存
_RSA_KEY_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data", "rsa_private_key.pem"
)


def _load_or_generate_private_key():
    """加载持久化的 RSA 私钥，不存在时生成并保存

    这样容器重启后 RSA 密钥对保持稳定，浏览器缓存的公钥不会失效。
    """
    # 尝试加载已有私钥
    if os.path.exists(_RSA_KEY_PATH):
        try:
            with open(_RSA_KEY_PATH, "rb") as f:
                key = serialization.load_pem_private_key(f.read(), password=None)
            logger.info("已加载持久化的 RSA 传输密钥")
            return key
        except Exception as e:
            logger.warning(f"加载 RSA 私钥失败，将重新生成: {e}")

    # 生成新私钥并持久化
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    # 确保目录存在
    key_dir = os.path.dirname(_RSA_KEY_PATH)
    if key_dir:
        os.makedirs(key_dir, exist_ok=True)

    # 保存私钥（PEM 格式，无密码保护，文件权限 600）
    key_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    try:
        with open(_RSA_KEY_PATH, "wb") as f:
            f.write(key_pem)
        os.chmod(_RSA_KEY_PATH, 0o600)
        logger.info(f"RSA 传输密钥已持久化到 {_RSA_KEY_PATH}")
    except Exception as e:
        logger.error(f"持久化 RSA 私钥失败（将使用内存中的密钥）: {e}")

    return key


_private_key = _load_or_generate_private_key()


def get_public_key_pem() -> str:
    """返回前端可导入的 RSA 公钥 PEM"""
    public_key = _private_key.public_key()
    return public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")


def decrypt_transport_secret(ciphertext_b64: str) -> str:
    """解密前端 RSA-OAEP 加密后的 Base64 密文。

    支持两种输入格式：
    1. RSA-OAEP 加密的 Base64 密文（HTTPS / localhost 下 crypto.subtle 可用时）
    2. 明文 SHA-256 摘要（64 位十六进制，HTTP 局域网下 crypto.subtle 不可用时的 fallback）

    格式2会记录审计警告，因为摘要未经 RSA 加密，存在被嗅探的风险。
    但不会拒绝请求 — 在 HTTP 局域网场景下强制 RSA 只会导致无法登录。

    解密失败（既不是合法密文也不是合法摘要）时抛 ValueError，
    由调用方拒绝请求，避免前端直接发送明文密码。
    """
    if not ciphertext_b64:
        raise ValueError("密文为空")

    # 格式2：明文 SHA-256 摘要（64 位十六进制）
    # HTTP 局域网下 crypto.subtle 不可用，前端 fallback 为直接发送摘要
    if len(ciphertext_b64) == 64 and all(c in '0123456789abcdef' for c in ciphertext_b64.lower()):
        logger.warning(
            "⚠️ 密码以明文摘要传输（未经 RSA 加密），可能为 HTTP 非安全上下文。"
            "建议使用 HTTPS 或通过 localhost 访问以启用 RSA 传输加密。"
        )
        return ciphertext_b64.lower()

    # 格式1：RSA-OAEP 加密的 Base64 密文
    try:
        ciphertext = base64.b64decode(ciphertext_b64, validate=True)
    except Exception as e:
        raise ValueError("密文不是合法的 Base64") from e
    try:
        plaintext = _private_key.decrypt(
            ciphertext,
            padding.OAEP(
                mgf=padding.MGF1(algorithm=hashes.SHA256()),
                algorithm=hashes.SHA256(),
                label=None,
            ),
        )
        return plaintext.decode("utf-8")
    except Exception as e:
        raise ValueError("RSA 解密失败：密码未使用公钥加密或已损坏") from e
