from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime


# ============================================================
# 资产相关
# ============================================================

class AssetCreate(BaseModel):
    hostname: str = Field(..., min_length=2, max_length=100)
    ip: str = Field(..., pattern=r'^(\d{1,3}\.){3}\d{1,3}$')
    port: int = Field(default=22, ge=1, le=65535)
    username: str = Field(..., min_length=1, max_length=50)
    password: Optional[str] = None
    ssh_key: Optional[str] = None
    ssh_key_path: Optional[str] = None
    ssh_cert: Optional[str] = None
    ssh_cert_path: Optional[str] = None
    sudo_password: Optional[str] = None
    sudo_enabled: bool = False
    jump_enabled: bool = False
    jump_host: Optional[str] = None
    jump_port: int = Field(default=22, ge=1, le=65535)
    jump_username: Optional[str] = None
    jump_password: Optional[str] = None
    jump_ssh_key: Optional[str] = None
    jump_ssh_key_path: Optional[str] = None
    jump_ssh_cert: Optional[str] = None
    jump_ssh_cert_path: Optional[str] = None
    os: Optional[str] = None
    cpu: Optional[str] = None
    memory: Optional[str] = None
    disk: Optional[str] = None
    env: str = Field(default="prod")
    role: Optional[str] = None
    owner: Optional[str] = None
    team: Optional[str] = None
    remark: Optional[str] = None
    expected_host_key: Optional[str] = None


class AssetUpdate(BaseModel):
    hostname: Optional[str] = Field(None, min_length=2, max_length=100)
    ip: Optional[str] = Field(None, pattern=r'^(\d{1,3}\.){3}\d{1,3}$')
    port: Optional[int] = Field(None, ge=1, le=65535)
    username: Optional[str] = Field(None, min_length=1, max_length=50)
    password: Optional[str] = None
    ssh_key: Optional[str] = None
    ssh_key_path: Optional[str] = None
    ssh_cert: Optional[str] = None
    ssh_cert_path: Optional[str] = None
    sudo_password: Optional[str] = None
    sudo_enabled: Optional[bool] = None
    jump_enabled: Optional[bool] = None
    jump_host: Optional[str] = None
    jump_port: Optional[int] = Field(None, ge=1, le=65535)
    jump_username: Optional[str] = None
    jump_password: Optional[str] = None
    jump_ssh_key: Optional[str] = None
    jump_ssh_key_path: Optional[str] = None
    jump_ssh_cert: Optional[str] = None
    jump_ssh_cert_path: Optional[str] = None
    os: Optional[str] = None
    cpu: Optional[str] = None
    memory: Optional[str] = None
    disk: Optional[str] = None
    env: Optional[str] = None
    role: Optional[str] = None
    owner: Optional[str] = None
    team: Optional[str] = None
    status: Optional[str] = None
    remark: Optional[str] = None
    expected_host_key: Optional[str] = None


class AssetResponse(BaseModel):
    id: int
    hostname: str
    ip: str
    port: int
    username: str
    ssh_key_path: Optional[str]
    ssh_cert_path: Optional[str]
    jump_enabled: bool
    jump_host: Optional[str]
    jump_port: Optional[int]
    jump_username: Optional[str]
    jump_ssh_key_path: Optional[str]
    jump_ssh_cert_path: Optional[str]
    os: Optional[str]
    cpu: Optional[str]
    memory: Optional[str]
    disk: Optional[str]
    env: str
    role: Optional[str]
    owner: Optional[str]
    team: Optional[str]
    status: str
    last_check: Optional[datetime]
    remark: Optional[str]
    expected_host_key: Optional[str]
    created_at: datetime
    updated_at: Optional[datetime]
    
    class Config:
        from_attributes = True


# ============================================================
# 用户与远程连接权限
# ============================================================

class UserCreate(BaseModel):
    username: str = Field(..., min_length=2, max_length=50)
    password: str = Field(..., min_length=1, max_length=1024, description="RSA-OAEP 加密后的密码摘要")
    display_name: Optional[str] = Field(None, max_length=80)
    email: Optional[str] = Field(None, max_length=120)
    role: str = Field(default="operator", pattern=r"^(admin|operator|developer|tester|other)$")
    team: Optional[str] = Field(None, max_length=50)
    is_active: bool = True
    remark: Optional[str] = None


class UserUpdate(BaseModel):
    password: Optional[str] = Field(None, min_length=1, max_length=1024, description="RSA-OAEP 加密后的密码摘要")
    display_name: Optional[str] = Field(None, max_length=80)
    email: Optional[str] = Field(None, max_length=120)
    role: Optional[str] = Field(None, pattern=r"^(admin|operator|developer|tester|other)$")
    team: Optional[str] = Field(None, max_length=50)
    is_active: Optional[bool] = None
    remark: Optional[str] = None


class UserResponse(BaseModel):
    id: int
    username: str
    display_name: Optional[str]
    email: Optional[str]
    role: str
    team: Optional[str]
    is_active: bool
    remark: Optional[str]
    created_at: datetime
    updated_at: Optional[datetime]

    class Config:
        from_attributes = True


class UserAssetPermissionItem(BaseModel):
    asset_id: int
    can_connect: bool = False
    can_execute: bool = False
    allow_sensitive_commands: bool = False
    allow_high_risk_commands: bool = False
    blocked_commands: Optional[str] = None
    remark: Optional[str] = None


class UserAssetPermissionUpdate(BaseModel):
    permissions: List[UserAssetPermissionItem] = Field(default_factory=list)


class UserAssetPermissionResponse(BaseModel):
    id: int
    user_id: int
    asset_id: int
    can_connect: bool
    can_execute: bool
    allow_sensitive_commands: bool = False
    allow_high_risk_commands: bool = False
    blocked_commands: Optional[str] = None
    remark: Optional[str]
    created_at: datetime
    updated_at: Optional[datetime]

    class Config:
        from_attributes = True


class UserPermissionDetail(BaseModel):
    asset_id: int
    hostname: str
    ip: str
    env: Optional[str]
    role: Optional[str]
    can_connect: bool
    can_execute: bool
    allow_sensitive_commands: bool = False
    allow_high_risk_commands: bool = False
    blocked_commands: Optional[str] = None
    remark: Optional[str] = None


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=50)
    password: str = Field(..., min_length=1, max_length=1024, description="RSA-OAEP 加密后的密码摘要")


class AuthResponse(BaseModel):
    token: str
    user: UserResponse


# ============================================================
# 外部服务入口
# ============================================================

class ExternalServiceCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    url: str = Field(..., min_length=1, max_length=500)
    description: Optional[str] = None
    icon: Optional[str] = Field(None, max_length=50)
    category: str = Field(default="other")
    sort_order: int = 0
    is_active: bool = True


class ExternalServiceUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    url: Optional[str] = Field(None, min_length=1, max_length=500)
    description: Optional[str] = None
    icon: Optional[str] = Field(None, max_length=50)
    category: Optional[str] = None
    sort_order: Optional[int] = None
    is_active: Optional[bool] = None


class ExternalServiceResponse(BaseModel):
    id: int
    name: str
    url: str
    description: Optional[str]
    icon: Optional[str]
    category: str
    sort_order: int
    is_active: bool
    created_at: datetime
    updated_at: Optional[datetime]

    class Config:
        from_attributes = True


# ============================================================
# Grafana监控看板
# ============================================================

class GrafanaDashboardCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    uid: str = Field(..., min_length=1, max_length=100)
    slug: Optional[str] = Field(None, max_length=200)
    url: Optional[str] = Field(None, max_length=500)
    description: Optional[str] = None
    category: str = Field(default="general")
    tags: Optional[str] = Field(None, max_length=200)
    sort_order: int = 0
    is_active: bool = True


class GrafanaDashboardUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    uid: Optional[str] = Field(None, min_length=1, max_length=100)
    slug: Optional[str] = Field(None, max_length=200)
    url: Optional[str] = Field(None, max_length=500)
    description: Optional[str] = None
    category: Optional[str] = None
    tags: Optional[str] = Field(None, max_length=200)
    sort_order: Optional[int] = None
    is_active: Optional[bool] = None


class GrafanaDashboardResponse(BaseModel):
    id: int
    name: str
    uid: str
    slug: Optional[str]
    url: Optional[str]
    description: Optional[str]
    category: str
    tags: Optional[str]
    sort_order: int
    is_active: bool
    created_at: datetime
    updated_at: Optional[datetime]

    class Config:
        from_attributes = True


# ============================================================
# 通用
# ============================================================

class MessageResponse(BaseModel):
    message: str
    detail: Optional[str] = None


# ============================================================
# SSH远程连接相关
# ============================================================

class SSHTestRequest(BaseModel):
    """测试SSH连接请求"""
    timeout: int = Field(default=10, ge=1, le=60, description="连接超时时间(秒)")


class SSHCommandRequest(BaseModel):
    """执行远程命令请求"""
    command: str = Field(..., min_length=1, max_length=5000, description="要执行的命令")
    timeout: int = Field(default=30, ge=1, le=300, description="命令执行超时时间(秒)")


class SSHTestResponse(BaseModel):
    """测试SSH连接响应"""
    success: bool
    message: str
    hostname: str
    ip: str


class SSHCommandResponse(BaseModel):
    """执行远程命令响应"""
    success: bool
    hostname: str
    command: str
    stdout: Optional[str] = None
    stderr: Optional[str] = None
    exit_code: Optional[int] = None
    error: Optional[str] = None


# ============================================================
# 审计日志
# ============================================================

class AuditLogResponse(BaseModel):
    id: int
    username: str
    action: str
    resource_type: str
    resource_name: Optional[str]
    detail: Optional[str]
    ip_address: Optional[str]
    status: str
    created_at: datetime

    class Config:
        from_attributes = True
