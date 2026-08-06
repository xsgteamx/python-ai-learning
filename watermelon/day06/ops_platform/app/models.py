from sqlalchemy import Column, Integer, String, Text, DateTime, Boolean, Float
from sqlalchemy.sql import func
from app.database import Base as SQLAlchemyBase


class Asset(SQLAlchemyBase):
    """资产表（服务器台账）"""
    __tablename__ = "assets"
    
    id = Column(Integer, primary_key=True, index=True)
    hostname = Column(String(100), unique=True, nullable=False, index=True)
    ip = Column(String(20), nullable=False)
    port = Column(Integer, default=22)
    username = Column(String(50), nullable=False)
    password = Column(String(255), nullable=True)
    ssh_key = Column(Text, nullable=True)
    ssh_key_path = Column(String(500), nullable=True)
    ssh_cert = Column(Text, nullable=True)
    ssh_cert_path = Column(String(500), nullable=True)
    sudo_password = Column(String(255), nullable=True)
    sudo_enabled = Column(Boolean, default=False)

    jump_enabled = Column(Boolean, default=False)
    jump_host = Column(String(100), nullable=True)
    jump_port = Column(Integer, default=22)
    jump_username = Column(String(50), nullable=True)
    jump_password = Column(String(255), nullable=True)
    jump_ssh_key = Column(Text, nullable=True)
    jump_ssh_key_path = Column(String(500), nullable=True)
    jump_ssh_cert = Column(Text, nullable=True)
    jump_ssh_cert_path = Column(String(500), nullable=True)
    
    os = Column(String(50), nullable=True)
    cpu = Column(String(50), nullable=True)
    memory = Column(String(50), nullable=True)
    disk = Column(String(100), nullable=True)
    env = Column(String(20), default="prod")
    
    role = Column(String(50), nullable=True)
    owner = Column(String(50), nullable=True)
    team = Column(String(50), nullable=True)
    
    status = Column(String(20), default="unknown")
    last_check = Column(DateTime(timezone=True), nullable=True)

    # 预期主机密钥指纹（可选）：填写后连接时严格匹配，不走 TOFU 自动接受
    # 格式如 "ssh-ed25519 SHA256:xxxx"，通过 ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub 获取
    expected_host_key = Column(String(200), nullable=True)

    remark = Column(Text, nullable=True)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class User(SQLAlchemyBase):
    """用户表"""
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=True)
    display_name = Column(String(80), nullable=True)
    email = Column(String(120), nullable=True)
    role = Column(String(20), default="operator")
    team = Column(String(50), nullable=True)
    is_active = Column(Boolean, default=True)
    remark = Column(Text, nullable=True)
    # Token 版本号：修改密码 / 重置密码 / 强制下线时递增，旧 Token 失效
    token_version = Column(Integer, default=0, nullable=False)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class UserAssetPermission(SQLAlchemyBase):
    """用户资产远程连接权限表"""
    __tablename__ = "user_asset_permissions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, nullable=False, index=True)
    asset_id = Column(Integer, nullable=False, index=True)
    can_connect = Column(Boolean, default=False)
    can_execute = Column(Boolean, default=False)
    allow_sensitive_commands = Column(Boolean, default=False)
    allow_high_risk_commands = Column(Boolean, default=False)
    blocked_commands = Column(Text, nullable=True)
    remark = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class ExternalService(SQLAlchemyBase):
    """外部服务入口（快捷跳转）"""
    __tablename__ = "external_services"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    url = Column(String(500), nullable=False)
    description = Column(Text, nullable=True)
    icon = Column(String(50), nullable=True)
    category = Column(String(50), default="other")
    sort_order = Column(Integer, default=0)
    is_active = Column(Boolean, default=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class GrafanaDashboard(SQLAlchemyBase):
    """Grafana监控看板配置"""
    __tablename__ = "grafana_dashboards"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    uid = Column(String(100), nullable=False, index=True)  # Grafana dashboard UID
    slug = Column(String(200), nullable=True)  # URL slug
    url = Column(String(500), nullable=True)  # 完整路径，如 /d/xxx/yyy
    description = Column(Text, nullable=True)
    category = Column(String(50), default="general")
    tags = Column(String(200), nullable=True)  # 逗号分隔的标签
    sort_order = Column(Integer, default=0)
    is_active = Column(Boolean, default=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class AuditLog(SQLAlchemyBase):
    """审计日志表"""
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), nullable=False, index=True)
    action = Column(String(50), nullable=False, index=True)
    resource_type = Column(String(50), nullable=False)
    resource_name = Column(String(200), nullable=True)
    detail = Column(Text, nullable=True)
    ip_address = Column(String(50), nullable=True)
    status = Column(String(20), default="success")

    created_at = Column(DateTime(timezone=True), server_default=func.now())


class InspectHistory(SQLAlchemyBase):
    """集群巡检历史记录表"""
    __tablename__ = "inspect_history"

    id = Column(Integer, primary_key=True, index=True)
    task_name = Column(String(100), nullable=True)
    asset_id = Column(Integer, nullable=False, index=True)
    asset_hostname = Column(String(100), nullable=False)
    asset_ip = Column(String(20), nullable=False)
    result_name = Column(String(200), nullable=True)
    danger_count = Column(Integer, default=0)
    warning_count = Column(Integer, default=0)
    ignore_count = Column(Integer, default=0)
    rule_total = Column(Text, nullable=True)
    start_time = Column(String(50), nullable=True)
    end_time = Column(String(50), nullable=True)
    operator = Column(String(50), nullable=True)
    raw_summary = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
