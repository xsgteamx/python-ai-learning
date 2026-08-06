from pydantic_settings import BaseSettings
from pydantic import field_validator
from typing import Optional
import os
import secrets
import logging

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    # 应用
    APP_NAME: str = "运维平台"
    APP_VERSION: str = "1.0.0"
    # 优先从环境变量 / .env 文件读取；未配置时由 validator 生成临时密钥并告警
    SECRET_KEY: str = ""
    DEBUG: bool = True
    # 默认同源策略：留空时不注册 CORS 中间件，最安全；
    # 需要跨域访问时在 .env 中显式配置，例如 ALLOWED_ORIGINS=https://ops.example.com,https://ops2.example.com
    ALLOWED_ORIGINS: str = ""
    ALLOWED_HOSTS: str = "*"

    # 数据库连接（支持两种方式）
    # 方式1：直接配置 DATABASE_URL（完整连接串）
    # 方式2：通过 DB_HOST/DB_PORT/DB_USER/DB_PASSWORD/DB_NAME 分散配置，由程序构建 URL
    # 本地开发默认使用 SQLite，Docker 部署通过 .env 切换为 MySQL
    DATABASE_URL: Optional[str] = None  # 如果提供则优先使用
    DB_HOST: str = ""
    DB_PORT: int = 3306
    DB_USER: str = ""
    DB_PASSWORD: str = ""
    DB_NAME: str = "ops_platform"

    # 外部服务
    PROMETHEUS_URL: str = "http://192.168.8.10:30090/"
    GRAFANA_URL: str = "http://192.168.8.10:32300/"
    JENKINS_URL: str = "http://192.168.8.8:10112/"

    # Grafana API（用于搜索仪表板、获取面板数据）
    GRAFANA_API_KEY: str = ""

    class Config:
        env_file = ".env"
        extra = "ignore"  # 忽略 .env 中的未知字段

    @field_validator("SECRET_KEY")
    @classmethod
    def _ensure_secret_key(cls, v: str) -> str:
        """SECRET_KEY 未通过环境变量或 .env 配置时，生成临时密钥并告警

        此 validator 在 pydantic 读取环境变量和 .env 文件之后执行，
        因此能准确判断 SECRET_KEY 是否真的被配置。
        生产部署必须通过环境变量或 .env 文件显式指定固定的 SECRET_KEY，
        否则重启后已加密的 SSH/sudo 密码将无法解密。
        """
        if v:
            return v
        tmp_key = secrets.token_urlsafe(32)
        logger.warning(
            "⚠️  SECRET_KEY 未通过环境变量或 .env 文件配置，已生成临时密钥。"
            "生产环境请务必在 .env 或环境变量中设置固定的 SECRET_KEY，"
            "否则重启后已加密的 SSH/sudo 密码将无法解密。"
        )
        return tmp_key

    @property
    def database_url(self) -> str:
        """动态构建数据库连接 URL（MySQL 或 SQLite）

        - 如果显式设置了 DATABASE_URL，直接使用
        - 否则如果 DB_HOST 非空，从分散字段构建 MySQL URL
        - 否则回退到本地 SQLite（ops.db）
        """
        if self.DATABASE_URL:
            return self.DATABASE_URL
        # Docker 部署时通过 .env 注入 DB_HOST/DB_PASSWORD 等，切换为 MySQL
        if self.DB_HOST:
            return f"mysql+pymysql://{self.DB_USER}:{self.DB_PASSWORD}@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}?charset=utf8mb4"
        # 本地开发默认使用 SQLite
        return "sqlite:///./ops.db"


settings = Settings()
