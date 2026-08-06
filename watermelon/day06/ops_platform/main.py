from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware
from contextlib import asynccontextmanager
from sqlalchemy import inspect, text
import hashlib
import logging

from app.database import engine, Base, SessionLocal
from app.models import User, ExternalService, Asset
from app.routes import assets, audit, auth, dashboard, services, users
from app.routes import host_keys as host_keys_routes
from app.routes import inspect as inspect_routes
from app.config import settings
from app.security import hash_password, verify_password
from app.services.crypto import encrypt_password, encrypt_secret, is_encrypted


def _sha256_hex(text: str) -> str:
    """对文本做 SHA-256 哈希，返回十六进制字符串（与前端 sha256Hex 对齐）"""
    return hashlib.sha256(text.encode()).hexdigest()


def init_database():
    """初始化数据库表、兼容旧表结构，并创建初始管理员"""
    Base.metadata.create_all(bind=engine)

    inspector = inspect(engine)
    if "users" in inspector.get_table_names():
        user_columns = {col["name"] for col in inspector.get_columns("users")}
        if "password_hash" not in user_columns:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE users ADD COLUMN password_hash VARCHAR(255)"))
        if "token_version" not in user_columns:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE users ADD COLUMN token_version INTEGER DEFAULT 0"))

    if "assets" in inspector.get_table_names():
        asset_columns = {col["name"] for col in inspector.get_columns("assets")}
        asset_migrations = {
            "jump_enabled": "BOOLEAN DEFAULT 0",
            "jump_host": "VARCHAR(100)",
            "jump_port": "INTEGER DEFAULT 22",
            "jump_username": "VARCHAR(50)",
            "jump_password": "VARCHAR(255)",
            "jump_ssh_key": "TEXT",
            "jump_ssh_key_path": "VARCHAR(500)",
            "ssh_cert": "TEXT",
            "ssh_cert_path": "VARCHAR(500)",
            "jump_ssh_cert": "TEXT",
            "jump_ssh_cert_path": "VARCHAR(500)",
            "sudo_password": "VARCHAR(255)",
            "sudo_enabled": "BOOLEAN DEFAULT 0",
            "expected_host_key": "VARCHAR(200)",
        }
        with engine.begin() as conn:
            for column, column_type in asset_migrations.items():
                if column not in asset_columns:
                    conn.execute(text(f"ALTER TABLE assets ADD COLUMN {column} {column_type}"))

    if "user_asset_permissions" in inspector.get_table_names():
        permission_columns = {col["name"] for col in inspector.get_columns("user_asset_permissions")}
        permission_migrations = {
            "allow_sensitive_commands": "BOOLEAN DEFAULT 0",
            "blocked_commands": "TEXT",
        }
        with engine.begin() as conn:
            for column, column_type in permission_migrations.items():
                if column not in permission_columns:
                    conn.execute(text(f"ALTER TABLE user_asset_permissions ADD COLUMN {column} {column_type}"))

    db = SessionLocal()
    try:
        # 迁移已有明文敏感字段为密文：密码 / 私钥 / 证书
        assets_to_migrate = db.query(Asset).filter(
            (Asset.password.isnot(None)) & (Asset.password != "") |
            (Asset.jump_password.isnot(None)) & (Asset.jump_password != "") |
            (Asset.sudo_password.isnot(None)) & (Asset.sudo_password != "") |
            (Asset.ssh_key.isnot(None)) & (Asset.ssh_key != "") |
            (Asset.jump_ssh_key.isnot(None)) & (Asset.jump_ssh_key != "") |
            (Asset.ssh_cert.isnot(None)) & (Asset.ssh_cert != "") |
            (Asset.jump_ssh_cert.isnot(None)) & (Asset.jump_ssh_cert != "")
        ).all()
        migrated_count = 0
        # 密码类字段
        password_fields = ("password", "jump_password", "sudo_password")
        # 私钥 / 证书类字段
        secret_fields = ("ssh_key", "jump_ssh_key", "ssh_cert", "jump_ssh_cert")
        for asset in assets_to_migrate:
            for field in password_fields:
                val = getattr(asset, field, None)
                if val and not is_encrypted(val):
                    setattr(asset, field, encrypt_password(val))
                    migrated_count += 1
            for field in secret_fields:
                val = getattr(asset, field, None)
                if val and not is_encrypted(val):
                    setattr(asset, field, encrypt_secret(val))
                    migrated_count += 1
        if migrated_count:
            db.commit()
            print(f"🔐 已加密 {migrated_count} 个明文敏感字段（密码/私钥/证书）")

        if db.query(User).filter(User.role == "admin").count() == 0:
            # 生成随机初始密码，避免弱口令 admin123456 被爆破
            import secrets as _secrets
            import string as _string
            _INIT_PWD_ALPHABET = _string.ascii_letters + _string.digits
            initial_password = ''.join(_secrets.choice(_INIT_PWD_ALPHABET) for _ in range(16))
            admin = User(
                username="admin",
                password_hash=hash_password(_sha256_hex(initial_password)),
                display_name="系统管理员",
                role="admin",
                is_active=True,
                remark="系统初始化管理员，请首次登录后立即修改密码"
            )
            db.add(admin)
            db.commit()
            print("=" * 60)
            print("🔐 初始管理员账号已创建")
            print(f"   用户名: admin")
            print(f"   初始密码: {initial_password}")
            print("   ⚠️  请立即登录并修改密码！此密码仅显示一次。")
            print("=" * 60)
            import logging as _logging
            _logging.getLogger(__name__).warning(
                f"初始管理员账号已创建，用户名: admin，初始密码已打印到控制台，请立即登录修改。"
            )

        # 首次启动时，根据配置文件中的外部服务地址预置快捷入口
        if db.query(ExternalService).count() == 0:
            default_services = [
                ExternalService(name="Jenkins", url=settings.JENKINS_URL, description="持续集成/部署", icon="🔧", category="ci", sort_order=1),
                ExternalService(name="Prometheus", url=settings.PROMETHEUS_URL, description="监控指标采集", icon="📊", category="monitor", sort_order=2),
                ExternalService(name="Grafana", url=settings.GRAFANA_URL, description="监控可视化看板", icon="📈", category="monitor", sort_order=3),
            ]
            db.add_all(default_services)
            db.commit()
    finally:
        db.close()


init_database()


@asynccontextmanager
async def lifespan(app: FastAPI):
    print(f"🚀 {settings.APP_NAME} v{settings.APP_VERSION} 启动中...")
    yield
    print(f"👋 {settings.APP_NAME} 正在关闭...")


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    lifespan=lifespan,
)

def _split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


# CORS：默认同源（ALLOWED_ORIGINS 为空时不注册 CORS 中间件，最安全）
# 需要跨域访问时在 .env 中显式配置 ALLOWED_ORIGINS=https://a.example.com,https://b.example.com
allowed_origins = _split_csv(settings.ALLOWED_ORIGINS)
allowed_hosts = _split_csv(settings.ALLOWED_HOSTS) or ["*"]

app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=allowed_hosts,
)

if allowed_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["Authorization", "Content-Type"],
    )


@app.middleware("http")
async def add_security_headers(request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
    response.headers["Cache-Control"] = "no-store"
    return response


# 全局异常处理器：未捕获的异常不向客户端暴露堆栈和内部信息
_logger = logging.getLogger(__name__)


@app.exception_handler(Exception)
async def _global_exception_handler(request: Request, exc: Exception):
    _logger.exception(f"未处理异常 [{request.method} {request.url.path}]: {exc}")
    # HTTPException 由 FastAPI 自身处理器优先处理，不会走到这里
    return JSONResponse(
        status_code=500,
        content={"detail": "服务器内部错误，请联系管理员"},
    )

# 注册路由
app.include_router(auth.router)
app.include_router(assets.router)
app.include_router(audit.router)
app.include_router(users.router)
app.include_router(services.router)
app.include_router(dashboard.router)
app.include_router(host_keys_routes.router)
app.include_router(inspect_routes.router)

# 静态文件
app.mount("/static", StaticFiles(directory="static", html=True), name="static")


@app.get("/")
async def root():
    return RedirectResponse(url="/static/index.html")


@app.get("/health")
async def health_check():
    return {"status": "healthy"}
