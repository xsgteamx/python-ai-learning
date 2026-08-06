# 台账管理功能使用说明

## 功能概述

运维平台现在提供了完整的**服务器台账管理**功能，包括：

1. **服务器信息管理** - 添加、编辑、删除服务器信息
2. **服务器分类** - 支持按环境和角色进行分类
3. **远程连接** - 通过SSH连接到远程服务器并执行命令
4. **状态监控** - 检测服务器在线状态

---

## API 接口文档

### 基础URL
```
http://127.0.0.1:8000
```

### API文档
- Swagger UI: http://127.0.0.1:8000/docs
- ReDoc: http://127.0.0.1:8000/redoc

---

## 1. 服务器台账管理

### 1.1 创建服务器台账

**POST** `/api/assets/`

请求示例：
```json
{
  "hostname": "web-server-01",
  "ip": "192.168.1.100",
  "port": 22,
  "username": "root",
  "password": "your_password",  // 或使用 ssh_key
  "jump_enabled": true,
  "jump_host": "1.2.3.4",
  "jump_port": 22,
  "jump_username": "jumpuser",
  "jump_password": "jump_password",
  "os": "CentOS 7",
  "cpu": "4核",
  "memory": "8GB",
  "disk": "100GB",
  "env": "prod",  // prod/test/dev/staging/uat
  "role": "web",  // web/app/db/cache/mq/storage/lb/monitor/ci/other
  "owner": "张三",
  "team": "运维组",
  "remark": "主Web服务器"
}
```

跳板机字段说明：
- `jump_enabled`: 是否启用跳板机
- `jump_host`: 公网跳板机地址
- `jump_port`: 公网跳板机 SSH 端口
- `jump_username`: 跳板机 SSH 用户名
- `jump_password` / `jump_ssh_key` / `jump_ssh_key_path`: 跳板机认证信息，三选一
- 目标服务器 `ip` 可以填写内网 IP，例如 `10.0.0.12`，系统会先连公网跳板机，再通过跳板机访问该内网 IP

密钥使用说明：
- 如果密钥文件在当前浏览器所在电脑上，建议在前端点击“选择密钥文件导入”，系统会读取文件内容并保存到 `ssh_key` 或 `jump_ssh_key`
- `ssh_key_path` 和 `jump_ssh_key_path` 表示后端服务进程能访问到的文件路径，不是浏览器本机路径
- 浏览器安全机制不会向网页暴露用户电脑上的完整文件路径，因此图形化选择文件采用“导入密钥内容”的方式实现

响应示例：
```json
{
  "id": 1,
  "hostname": "web-server-01",
  "ip": "192.168.1.100",
  "port": 22,
  "username": "root",
  "os": "CentOS 7",
  "env": "prod",
  "role": "web",
  "status": "unknown",
  "created_at": "2026-07-29T10:30:00"
}
```

### 1.2 查询服务器列表

**GET** `/api/assets/`

查询参数：
- `skip`: 跳过记录数（默认0）
- `limit`: 返回记录数（默认100）
- `env`: 按环境过滤（prod/test/dev等）
- `role`: 按角色过滤（web/app/db等）
- `status`: 按状态过滤（online/offline/unknown）
- `search`: 搜索关键字（匹配主机名或IP）

示例：
```
GET /api/assets/?env=prod&role=web&search=server-01
```

### 1.3 更新服务器信息

**PUT** `/api/assets/{asset_id}`

### 1.4 删除服务器

**DELETE** `/api/assets/{asset_id}`

---

## 2. SSH远程连接功能

> 远程连接接口必须携带登录令牌，后端会根据当前登录用户判断是否具备该服务器权限，不再由前端传 `user_id`。

### 2.1 测试SSH连接

**POST** `/api/assets/{asset_id}/test-connection`

请求示例：
```json
{
  "timeout": 10
}
```

响应示例：
```json
{
  "success": true,
  "message": "连接成功",
  "hostname": "web-server-01",
  "ip": "192.168.1.100"
}
```

### 2.2 执行远程命令

**POST** `/api/assets/{asset_id}/execute`

请求示例：
```json
{
  "command": "uptime",
  "timeout": 30
}
```

响应示例：
```json
{
  "success": true,
  "hostname": "web-server-01",
  "command": "uptime",
  "stdout": " 10:30:01 up 10 days,  3:45,  2 users,  load average: 0.00, 0.01, 0.05",
  "stderr": "",
  "exit_code": 0
}
```

---

## 3. 分类管理

### 3.1 获取环境分类列表

**GET** `/api/assets/categories/environments`

响应示例：
```json
[
  {"value": "prod", "label": "生产环境", "description": "生产环境服务器"},
  {"value": "test", "label": "测试环境", "description": "测试环境服务器"},
  {"value": "dev", "label": "开发环境", "description": "开发环境服务器"},
  {"value": "staging", "label": "预发布环境", "description": "预发布环境服务器"},
  {"value": "uat", "label": "UAT环境", "description": "用户验收测试环境"}
]
```

### 3.2 获取角色分类列表

**GET** `/api/assets/categories/roles`

响应示例：
```json
[
  {"value": "web", "label": "Web服务器", "description": "Web前端服务器"},
  {"value": "app", "label": "应用服务器", "description": "应用服务器"},
  {"value": "db", "label": "数据库服务器", "description": "数据库服务器"}
]
```

### 3.3 获取状态分类列表

**GET** `/api/assets/categories/statuses`

---

## 4. 使用示例

### 示例0：登录获取令牌

首次启动会自动创建默认管理员：
- 用户名：`admin`
- 密码：`admin123456`

```bash
curl -X POST "http://127.0.0.1:8000/api/auth/login" \
  -H "Content-Type: application/json" \
  -d '{
    "username": "admin",
    "password": "admin123456"
  }'
```

响应中的 `token` 用于后续请求：

```bash
Authorization: Bearer <token>
```

### 示例1：添加生产环境Web服务器

```bash
curl -X POST "http://127.0.0.1:8000/api/assets/" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <token>" \
  -d '{
    "hostname": "prod-web-01",
    "ip": "192.168.1.100",
    "username": "root",
    "password": "your_password",
    "env": "prod",
    "role": "web"
  }'
```

### 示例2：测试服务器连接

```bash
curl -X POST "http://127.0.0.1:8000/api/assets/1/test-connection" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <token>" \
  -d '{"timeout": 10}'
```

### 示例3：在远程服务器上执行命令

```bash
curl -X POST "http://127.0.0.1:8000/api/assets/1/execute" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <token>" \
  -d '{
    "command": "df -h",
    "timeout": 30
  }'
```

---

## 5. 用户管理与远程权限

用户管理接口仅管理员可访问，普通用户登录后只能查看平台并按已分配权限连接服务器。

### 5.1 创建用户

**POST** `/api/users/`

请求示例：
```json
{
  "username": "zhangsan",
  "password": "initial_password",
  "display_name": "张三",
  "role": "operator",
  "team": "运维组",
  "email": "zhangsan@example.com",
  "is_active": true
}
```

用户角色：
- `admin`: 管理员，默认拥有全部服务器远程连接和命令执行权限
- `operator`: 运维人员，需要按服务器分配权限
- `viewer`: 只读用户，默认无远程权限，可按服务器单独授权

### 5.2 查询用户列表

**GET** `/api/users/`

查询参数：
- `is_active`: 按启用状态过滤
- `search`: 搜索用户名、姓名或邮箱
- `skip` / `limit`: 分页参数

### 5.3 修改用户或重置密码

**PUT** `/api/users/{user_id}`

编辑用户时可传 `password` 重置密码，不传则保持原密码不变。

### 5.4 分配用户远程权限

**PUT** `/api/users/{user_id}/permissions`

请求示例：
```json
{
  "permissions": [
    {
      "asset_id": 1,
      "can_connect": true,
      "can_execute": false
    },
    {
      "asset_id": 2,
      "can_connect": true,
      "can_execute": true
    }
  ]
}
```

权限划分：
- `can_connect`: 允许测试 SSH 连接和打开远程终端
- `can_execute`: 允许通过 SSH 执行远程命令；拥有执行权限时也应具备连接权限

### 5.5 查询权限明细

**GET** `/api/users/{user_id}/permission-details`

返回该用户在所有服务器上的权限勾选状态，前端“用户管理 -> 权限”弹窗使用该接口渲染权限矩阵。

---

## 6. 数据库模型

### Asset 表字段

| 字段 | 类型 | 说明 |
|------|------|------|
| id | Integer | 主键ID |
| hostname | String(100) | 主机名（唯一） |
| ip | String(20) | IP地址 |
| port | Integer | SSH端口（默认22） |
| username | String(50) | 登录用户名 |
| password | String(255) | SSH密码（可选） |
| ssh_key | Text | SSH私钥（可选） |
| jump_enabled | Boolean | 是否启用跳板机 |
| jump_host | String(100) | 跳板机公网地址 |
| jump_port | Integer | 跳板机 SSH 端口 |
| jump_username | String(50) | 跳板机用户名 |
| jump_password | String(255) | 跳板机密码（可选） |
| jump_ssh_key | Text | 跳板机私钥内容（可选） |
| jump_ssh_key_path | String(500) | 跳板机私钥文件路径（可选） |
| os | String(50) | 操作系统 |
| cpu | String(50) | CPU信息 |
| memory | String(50) | 内存信息 |
| disk | String(100) | 磁盘信息 |
| env | String(20) | 环境分类 |
| role | String(50) | 角色分类 |
| owner | String(50) | 负责人 |
| team | String(50) | 所属团队 |
| status | String(20) | 状态（online/offline/unknown） |
| last_check | DateTime | 最后检查时间 |
| remark | Text | 备注 |

### User 表字段

| 字段 | 类型 | 说明 |
|------|------|------|
| id | Integer | 主键ID |
| username | String(50) | 用户名（唯一） |
| password_hash | String(255) | 登录密码哈希 |
| display_name | String(80) | 显示姓名 |
| email | String(120) | 邮箱 |
| role | String(20) | 用户角色（admin/operator/viewer） |
| team | String(50) | 所属团队 |
| is_active | Boolean | 是否启用 |
| remark | Text | 备注 |

### UserAssetPermission 表字段

| 字段 | 类型 | 说明 |
|------|------|------|
| id | Integer | 主键ID |
| user_id | Integer | 用户ID |
| asset_id | Integer | 服务器资产ID |
| can_connect | Boolean | 是否允许远程连接 |
| can_execute | Boolean | 是否允许执行远程命令 |
| remark | Text | 备注 |

---

## 7. 安全建议

1. **密码加密**: 建议对数据库中的密码字段进行加密存储
2. **SSH密钥认证**: 优先使用SSH密钥认证而非密码认证
3. **权限控制**: 远程连接和命令执行应始终携带用户ID并进行权限校验
4. **日志审计**: 记录所有SSH操作日志
5. **网络隔离**: 限制API访问来源IP

---

## 8. 后续功能规划

- [ ] 批量导入服务器信息（CSV/Excel）
- [ ] 服务器分组管理
- [ ] 批量命令执行
- [ ] 实时SSH终端（WebSocket）
- [ ] 服务器监控和告警
- [ ] 操作日志审计

---

## 启动服务

```bash
# 安装依赖
pip install -r requirements.txt

# 启动服务
uvicorn main:app --reload

# 或使用Python直接启动
python main.py
```

服务启动后访问：
- API服务: http://127.0.0.1:8000
- API文档: http://127.0.0.1:8000/docs
