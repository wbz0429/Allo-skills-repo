---
name: query-dfcode-usage
description: 使用 SQL 直接查询生产数据库中的 dfcode usage_records，按用户、模型、日期范围等维度统计用量。当需要排查用量数据、对账 MAAS、或按员工/模型/日期查使用记录时使用此 skill。
---

# Query DFCode Usage

## 环境要求

执行此 skill 前需确保以下条件已满足：

### 1. SSH 访问生产服务器

需要有 `root@119.45.112.196` 的 SSH 权限。使用 wbz0429 的 SSH key：

```bash
# 将 wbz0429 的 SSH 私钥添加到本地 ssh-agent 或配置 ~/.ssh/config：
Host dfcode-prod
  HostName 119.45.112.196
  User root
  IdentityFile ~/.ssh/wbz0429_prod_key
```

测试连接：

```bash
ssh root@119.45.112.196 "echo ok"
# 或
ssh dfcode-prod "echo ok"
```

> 如果还没有服务器访问权限或 SSH key，联系 wbz0429 获取。

### 2. 服务器上要有 psql

生产服务器需安装 PostgreSQL 客户端：

```bash
ssh root@119.45.112.196 "which psql"
```

如果未安装：

```bash
ssh root@119.45.112.196 "apt install -y postgresql-client"
```

## Overview

直接通过 SSH 连接 dfcode-admin 生产数据库，执行 SQL 查询 `usage_records` 表。不使用 API（API 需要 admin JWT），直接用 `psql` 查库。

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
ssh root@119.45.112.196
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
