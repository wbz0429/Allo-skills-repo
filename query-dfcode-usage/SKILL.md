---
name: query-dfcode-usage
description: 使用 SQL 直接查询生产数据库中的 dfcode usage_records，按用户、模型、日期范围等维度统计用量。当需要排查用量数据、对账 MAAS、或按员工/模型/日期查使用记录时使用此 skill。
---

# Query DFCode Usage

## 环境要求

执行此 skill 前需确保以下条件已满足：

### 1. SSH 访问生产服务器

需要有 `root@119.45.112.196` 的 SSH 权限。生产服务器已配置 wbz0429 的默认 SSH key（`~/.ssh/id_ed25519.pub`）。

其他团队成员需要把自己的 SSH 公钥加到服务器 `~/.ssh/authorized_keys` 中，联系 wbz0429 操作。

或直接使用 wbz0429 的 SSH 私钥（适用于已获取该私钥的同事）：

```bash
# 将 wbz0429 的 id_ed25519 私钥放到 ~/.ssh/ 下，然后配置 ~/.ssh/config：
Host dfcode-prod
  HostName 119.45.112.196
  User ubuntu
  IdentityFile ~/.ssh/id_ed25519
```

测试连接：

```bash
ssh ubuntu@119.45.112.196 "echo ok"
# 或
ssh dfcode-prod "echo ok"
```

> 如果还没有服务器访问权限，联系 wbz0429 添加你的 SSH 公钥或获取私钥。

### 2. 服务器上要有 psql

生产服务器需安装 PostgreSQL 客户端：

```bash
ssh ubuntu@119.45.112.196 "which psql"
```

如果未安装：

```bash
ssh ubuntu@119.45.112.196 "sudo apt install -y postgresql-client"
```

## Overview

直接通过 SSH 连接 dfcode-admin 生产数据库，执行 SQL 查询 `usage_records` 表。不使用 API（API 需要 admin JWT），直接用 `psql` 查库。

**备选方式：** 也可以通过 HTTP API 查询（需要 admin JWT），适合需要前端展示或自动化场景，见 [通过 API 查询](#通过-api-查询)。

## 生产服务器信息

| 项目 | 值 |
|------|-----|
| 服务器 IP | 119.45.112.196 |
| 代码目录 | /opt/dfcode-admin |
| 服务 | dfcode-admin.service |
| API 端口 | 4097 |

## 连接数据库

先 SSH 到服务器，再找到数据库连接信息：

```bash
ssh ubuntu@119.45.112.196
```

**获取数据库连接信息：**

```bash
cd /opt/dfcode-admin/packages/enterprise-server
cat .env | grep DATABASE_URL
```

**连接 PostgreSQL：**

```bash
psql "<DATABASE_URL>"
```

或者如果 DATABASE_URL 是完整连接串格式（postgresql://user:pass@host:port/dbname），直接用：

```bash
psql "$(grep DATABASE_URL /opt/dfcode-admin/packages/enterprise-server/.env | cut -d'=' -f2-)"
```

## 数据表结构

### usage_records

| 列名 | 类型 | 说明 |
|------|------|------|
| id | text (PK) | 使用记录 ID |
| org_id | text (FK → organizations.id) | 组织 ID |
| user_id | text (FK → users.id) | 用户 ID |
| session_id | text | 会话 ID |
| project_name | text | 项目名，如 dfcode、dfcode/summary、dfcode/title |
| model | text | 模型名，如 maas/gpt-5.5 |
| provider_id | text | 提供者 ID，如 maas、kkgpt |
| model_id | text | 模型 ID，如 gpt-5.5 |
| agent | text | agent 标识 |
| cost | decimal(12,6) | 费用（当前存 0） |
| tokens_input | integer | 输入 token |
| tokens_output | integer | 输出 token |
| tokens_reasoning | integer | reasoning token |
| tokens_cache_read | integer | 缓存读取 token |
| tokens_cache_write | integer | 缓存写入 token |
| tokens_total | integer | 总 token |
| created_at | timestamptz | 创建时间 |

### users

| 列名 | 类型 | 说明 |
|------|------|------|
| id | text (PK) | 用户 ID |
| org_id | text | 组织 ID |
| name | text | 用户名 |
| employee_id | text | 工号 |
| role | text | 角色 (owner/admin/member) |

## 常见查询

### 1. 查某个用户今日用量

```sql
SELECT
  provider_id,
  model_id,
  count(*) as requests,
  sum(tokens_total) as total_tokens,
  sum(tokens_input) as input_tokens,
  sum(tokens_output) as output_tokens
FROM usage_records
WHERE user_id = '<user_id>'
  AND created_at >= current_date
GROUP BY provider_id, model_id
ORDER BY requests DESC;
```

### 2. 按天统计用户用量趋势

```sql
SELECT
  date_trunc('day', created_at)::date as day,
  count(*) as requests,
  sum(tokens_total) as total_tokens
FROM usage_records
WHERE user_id = '<user_id>'
  AND created_at >= current_date - interval '7 days'
GROUP BY 1
ORDER BY 1 DESC;
```

### 3. 全组织今日用量汇总（按用户）

```sql
SELECT
  u.name,
  u.employee_id,
  ur.provider_id,
  count(*) as requests,
  sum(ur.tokens_total) as total_tokens
FROM usage_records ur
JOIN users u ON u.id = ur.user_id
WHERE ur.org_id = '<org_id>'
  AND ur.created_at >= current_date
GROUP BY u.name, u.employee_id, ur.provider_id
ORDER BY requests DESC
LIMIT 50;
```

### 4. 按项目分类统计（区分主对话/摘要/标题）

```sql
SELECT
  project_name,
  count(*) as requests,
  sum(tokens_total) as total_tokens
FROM usage_records
WHERE created_at >= current_date
GROUP BY project_name
ORDER BY requests DESC;
```

### 5. 查某段时间指定模型的所有记录

```sql
SELECT
  u.name,
  u.employee_id,
  ur.created_at,
  ur.project_name,
  ur.tokens_total
FROM usage_records ur
JOIN users u ON u.id = ur.user_id
WHERE ur.model = 'maas/gpt-5.5'
  AND ur.created_at >= '2026-05-01'
  AND ur.created_at < '2026-05-02'
ORDER BY ur.created_at DESC
LIMIT 100;
```

### 6. 与 MAAS 对账：查某个员工的 provider_id=maas 记录数

```sql
SELECT
  date_trunc('day', created_at)::date as day,
  count(*) as dfcode_maas_records,
  sum(tokens_total) as total_tokens
FROM usage_records
WHERE user_id = '<user_id>'
  AND provider_id = 'maas'
  AND created_at >= current_date - interval '7 days'
GROUP BY 1
ORDER BY 1 DESC;
```

## 注意事项

- `cost` 字段当前始终为 0，实际计费以 MAAS 侧为准
- `provider_id` 和 `model_id` 来自 CLI 上报的消息 metadata，不代表实际请求通道
- 对账 MAAS 时注意：DFCode `usage_records.count(*)` ≠ MAAS 真实计费请求数，需要对比 MAAS `usage_logs`
- 主对话用量的 `project_name` 为 `dfcode`，标题生成为 `dfcode/title`，摘要为 `dfcode/summary`
- 后台 `/usage/record` 会跳过 `tokens_total <= 0 && cost <= 0` 的记录，不写入

## 通过 API 查询

如果不想 SSH 直接查库，也可以用 HTTP API。Admin 用量接口路径为 `GET /api/v1/usage/`，需要 admin JWT。

### Admin JWT 鉴权

Admin JWT 需满足：
- 用户 `role` 为 `owner` 或 `admin`（不是 `member`）
- `type` 为 `access`
- JWT 签名 secret 来自服务器 `.env` 的 `JWT_SECRET`
- 请求头：`Authorization: Bearer <admin_jwt>`

**获取 admin JWT 的方式：**
1. 用 owner/admin 账号登录 dfcode-admin Web 前端，F12 从 network 请求头里拿 Bearer token
2. 或者直接 SSH 到服务器用脚本签发：

```bash
ssh ubuntu@119.45.112.196
cd /opt/dfcode-admin/packages/enterprise-server

# 找到 admin 用户的 user_id
ADMIN_USER_ID=$(bun -e "
  const { db } = require('./src/db');
  const { users } = require('./src/db/schema');
  const { eq } = require('drizzle-orm');
  (async () => {
    const [u] = await db.select().from(users).where(eq(users.role, 'owner')).limit(1);
    console.log(u.id);
    process.exit();
  })();
" 2>/dev/null)

# 用临时脚本签发 JWT（需先确认 JWT_SECRET）
source /opt/dfcode-admin/packages/enterprise-server/.env
```

### Admin 用量 API 参数

`GET /api/v1/usage/?<query>` (需要 admin JWT)

| 参数 | 类型 | 必须 | 说明 |
|------|------|------|------|
| userId | string | 否 | 按用户 ID 筛选 |
| model | string | 否 | 按模型筛选，如 `maas/gpt-5.5` |
| project | string | 否 | 按项目筛选，如 `dfcode` |
| from | string | 否 | 起始日期 `YYYY-MM-DD` |
| to | string | 否 | 结束日期 `YYYY-MM-DD` |
| page | number | 否 | 页码，默认 1 |
| pageSize | number | 否 | 每页条数，1-100，默认 20 |
| groupBy | string | 否 | 聚合维度：`user` / `model` / `project` / `day` |
| sortBy | string | 否 | 排序：`requests`(默认) / `tokens` / `cost` |

**示例：**

```bash
# 查某个用户今日用量
curl -s -H "Authorization: Bearer $ADMIN_JWT" \
  "https://dfcode-admin.yourdomain.com/api/v1/usage/?userId=user_xxx&from=$(date +%Y-%m-%d)" | jq

# 按用户聚合今日用量
curl -s -H "Authorization: Bearer $ADMIN_JWT" \
  "https://dfcode-admin.yourdomain.com/api/v1/usage/?from=$(date +%Y-%m-%d)&groupBy=user&sortBy=tokens" | jq

# 按天统计最近 7 天趋势
curl -s -H "Authorization: Bearer $ADMIN_JWT" \
  "https://dfcode-admin.yourdomain.com/api/v1/usage/?from=$(date -v-7d +%Y-%m-%d)&groupBy=day&sortBy=tokens" | jq
```

> 注意：API 只能查询当前 org 的数据（JWT 中 `orgId` 限定），无法跨组织查询。
