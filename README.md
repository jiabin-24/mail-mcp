# Mail MCP (Python + modelcontextprotocol)

这是一个邮件助手 MCP 服务，基于 Python 和官方 MCP Python SDK（`mcp` 包），并真实对接 Outlook（Microsoft Graph）。

当前版本实现：
- MCP 服务入口
- Outlook 邮箱读写（Microsoft Graph）
- 常见邮件助手基础工具（列目录、读邮件、搜索、写草稿、发草稿、撤销草稿）

## 1. 环境准备

要求：
- Python 3.10+

安装：

```bash
python -m venv .venv
.venv\\Scripts\\activate
pip install -U pip
pip install -e .
```

## 2. 启动服务

```bash
mail-mcp
```

或者：

```bash
python -m mail_mcp.server
```

> 服务进程本身默认使用 HTTP（streamable-http），推荐通过反向代理提供 HTTPS。

### 2.0 通过 Docker 部署到 Azure App Service

仓库已提供 [Dockerfile](Dockerfile) 和 [.dockerignore](.dockerignore)。

服务在容器内默认监听：

- Host: `0.0.0.0`
- Port: `80`（也兼容 App Service 注入的 `PORT`）
- MCP Path: `/mcp`

推荐流程：

1. 使用 ACR 云端构建镜像（无需本地 Docker）

```bash
az acr build \
  --registry <acr-name> \
  --image mail-mcp:latest \
  .
```

2. （可选）查看 ACR 中镜像标签

```bash
az acr repository show-tags \
  --name <acr-name> \
  --repository mail-mcp \
  --output table
```

3. 在 App Service 使用该镜像（Web App for Containers）

- Image source: ACR
- Image: `<acr-name>.azurecr.io/mail-mcp:latest`
- Container port: `80`
- Health check path: `/healthz`

4. MCP 客户端连接地址

- `https://<app-name>.azurewebsites.net/mcp`

5. 部署后验证

- `https://<app-name>.azurewebsites.net/`
- `https://<app-name>.azurewebsites.net/healthz`

### 2.1 Outlook 鉴权配置

服务会按以下优先级获取 Graph Token：

1. MCP 请求头中的 `Authorization: Bearer <token>`（推荐）
2. 环境变量 `OUTLOOK_ACCESS_TOKEN`（调试兜底）

可选环境变量：

- `GRAPH_BASE_URL`（默认 `https://graph.microsoft.com/v1.0`）
- `DELEGATED_TOKEN_LOG_MODE`（默认 `masked`，可选 `masked` / `full` / `none`）
- `DELEGATED_TOKEN_CACHE_TTL_SECONDS`（默认 `300`，token 校验结果缓存秒数）
- `MCP_EXPOSE_AGENTS_MD`（默认 `false`，设置为 `true` 后对外暴露 `mailbox_get_agents_md()` 工具，返回仓库根目录 `AGENTS.md` 内容）

### 2.1.1 配置分层（推荐：非敏感入库，敏感留在 App Service）

服务启动时按以下优先级加载配置（高 -> 低）：

1. 进程环境变量（例如 Azure App Service 的 App Settings）
2. 仓库根目录 `.env`（本地私有，不入库）
3. 当 `APP_ENV` 已设置时：`.env.<APP_ENV>`；否则：`.env.prod`

说明：

- 项目已支持将 `.env.prod` 提交到 Git（用于非敏感默认值）。
- 机密信息仍应只放在 App Service App Settings（或 Key Vault），不要写入 `.env.prod`。

建议放入 `.env.prod` 的示例（非敏感）：

- `MCP_HOST` / `MCP_PORT` / `MCP_PATH`
- `GRAPH_BASE_URL`
- `DELEGATED_TOKEN_LOG_MODE`
- `DELEGATED_TOKEN_CACHE_TTL_SECONDS`
- `MCP_EXPOSE_AGENTS_MD`

建议仅放在 App Service 的示例（敏感/租户强绑定）：

- `AZURE_CLIENT_SECRET`
- `MCP_OAUTH_CLIENT_SECRET`
- `OUTLOOK_ACCESS_TOKEN`
- `AZURE_CLIENT_ID` / `AZURE_TENANT_ID` / `AZURE_STORAGE_ACCOUNT_NAME`

Azure Table（定时发送队列）环境变量：

- `AZURE_STORAGE_ACCOUNT_NAME`（Storage Account 名称）
- `AZURE_STORAGE_TABLE_NAME`（可选，默认 `EmailSendQueue`）
- `AZURE_TENANT_ID`（Service Principal 租户 ID）
- `AZURE_CLIENT_ID`（Service Principal 客户端 ID）
- `AZURE_CLIENT_SECRET`（Service Principal 密钥）

Azure Table 所需 RBAC（Service Principal）：

- 建议最小权限：`Storage Table Data Contributor`
- 建议作用域：Storage Account 级别（支持表不存在时自动创建）
- 不建议仅分配 `Contributor`（管理面权限，通常不包含 Table 数据面读写）

触发批量发送（Service Principal）所需 Graph 应用权限：

- `Mail.Send`（Application）
- 建议同时配置 `Mail.ReadWrite`（Application）用于草稿与发送流程兼容
- 以上 Application 权限需管理员同意（Admin consent）

当前实现固定使用 `/me` 路由访问 Outlook 邮箱。

### 2.1.1 OAuth 2.0 Dynamic discovery（新增）

服务已支持 MCP OAuth 发现与动态客户端注册（DCR）。

当 `MCP_OAUTH_DYNAMIC_DISCOVERY_ENABLED=true` 且以下环境变量配置完整时，服务会自动启用：

- `MCP_PUBLIC_BASE_URL`（例如 `https://<app-name>.azurewebsites.net`）
- `MCP_OAUTH_ISSUER_URL`（可选，默认同 `MCP_PUBLIC_BASE_URL`）
- `MCP_OAUTH_CALLBACK_URL`（可选，默认 `${issuer}/oauth/callback`）
- `MCP_OAUTH_TENANT_ID`
- `MCP_OAUTH_CLIENT_ID`
- `MCP_OAUTH_CLIENT_SECRET`
- `MCP_OAUTH_ENTRA_SCOPES`（可选）

启用后会暴露以下端点：

- `/.well-known/oauth-authorization-server`
- `/.well-known/oauth-protected-resource`
- `/register`（动态客户端注册）
- `/authorize`
- `/token`
- `/revoke`
- `/oauth/callback`（与 Entra ID 交互的回调）

OAuth 动态客户端注册持久化（防止重启后 client_id 丢失）：

- 服务会优先将 DCR 注册得到的客户端信息写入 Azure Table，并在 `/authorize` 时回查
- 仅复用现有 Azure Table 配置：`AZURE_STORAGE_ACCOUNT_NAME` + `AZURE_TENANT_ID` + `AZURE_CLIENT_ID` + `AZURE_CLIENT_SECRET`
- OAuth 客户端注册信息写入固定表名：`OAuthClientRegistry`
- 若上述 `AZURE_*` 配置不完整，服务会回退为进程内存模式（重启后 DCR client_id 仍会失效）

说明：

- MCP 访问令牌由本服务签发并校验。
- 实际调用 Microsoft Graph 使用的是服务在 OAuth 登录过程中换取的用户委托令牌。
- 若未启用 Dynamic discovery 配置，服务保持兼容模式（直接接收 `Authorization: Bearer <Graph token>`）。

鉴权说明：

- `/mcp` 调用必须携带 `Authorization: Bearer <token>`
- 在直传 Graph token 模式下，服务会通过 Graph `GET /me` 校验 token 是否有效，校验失败返回 `401`
- `/` 与 `/healthz` 仍允许匿名访问（用于健康检查）

Token 至少需要这些 Graph 权限之一（按你调用场景）：

- 读取邮件：`Mail.Read`
- 写草稿/发送：`Mail.ReadWrite`, `Mail.Send`
- 读取日历：`Calendars.Read`
- 创建/修改日历：`Calendars.ReadWrite`

默认地址：
- Host: `127.0.0.1`
- Port: `80`
- MCP Path: `/mcp`
- 后端 URL: `http://127.0.0.1:80/mcp`

你也可以通过环境变量覆盖：

Windows CMD:

```cmd
set MCP_HOST=0.0.0.0
set MCP_PORT=9000
set MCP_PATH=/mcp
mail-mcp
```

Windows PowerShell:

```powershell
$env:MCP_HOST = "0.0.0.0"
$env:MCP_PORT = "80"
$env:MCP_PATH = "/mcp"
mail-mcp
```

### 2.2 通过反向代理提供 HTTPS（推荐）

思路：
- `mail-mcp` 只监听内网 HTTP（如 `127.0.0.1:80`）
- Nginx/Caddy 对外暴露 443，负责证书和 TLS

Nginx 示例：

```nginx
server {
  listen 443 ssl;
  server_name mcp.example.com;

  ssl_certificate     /etc/letsencrypt/live/mcp.example.com/fullchain.pem;
  ssl_certificate_key /etc/letsencrypt/live/mcp.example.com/privkey.pem;

  location ~ ^/mcp(/.*)?$ {
    proxy_pass http://127.0.0.1:80/mcp$1;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto https;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
  }
}
```

> 对外连接地址示例：`https://mcp.example.com/mcp`

### 2.3 定时任务批量触发发送（HTTP GET）

服务提供一个 HTTP GET 端点用于触发批量发送：

- `GET /jobs/dispatch`

行为说明：

- 查询 Azure Table `EmailSendQueue` 中状态为 `pending` 或 `scheduled` 的任务
- 仅处理已到计划发送时间的任务（`schedulesendtime <= 当前 UTC`），未到期任务跳过
- 使用 Service Principal 调用 Graph 发送对应草稿（`/users/{userupn}/messages/{draftId}/send`）
- 成功任务更新为 `sent` 并写入 `senttime`
- 失败任务更新为 `failed` 并写入 `lasterror`

## 3. 已提供的工具（Tools）

### 3.1 基础与健康检查

- `ping()`
  - 健康检查
- `mailbox_get_agents_md()`（仅当 `MCP_EXPOSE_AGENTS_MD=true` 时注册）
  - 返回仓库根目录 `AGENTS.md` 内容，便于 MCP 客户端读取 Agent 规则

### 3.2 邮件读取与检索

- `mailbox_list_folders()`
  - 列出文件夹（`inbox` / `drafts` / `sent`）
- `mailbox_list_messages(folder="inbox", limit=20)`
  - 列出邮件摘要
- `mailbox_get_message(message_id)`
  - 按 ID 获取邮件详情
- `mailbox_search(search=None, filter=None, folder="inbox", limit=20)`
  - 透传 Graph `$search` / `$filter` 查询邮件

### 3.3 草稿与发送

- `mailbox_compose(to, subject, body, cc=None, bcc=None)`
  - 生成草稿
- `mailbox_reply_compose(message_id, body)`
  - 基于原邮件生成回复草稿（自动保留历史上下文引用）
- `mailbox_update_draft(draft_id, to=None, subject=None, body=None, cc=None, bcc=None)`
  - 修改现有草稿（支持更新收件人、主题、正文、抄送、密送）
- `mailbox_send_draft(draft_id)`
  - 发送草稿并移到 `sent`
- `mailbox_revoke_draft(draft_id)`
  - 撤销草稿（删除草稿）

### 3.4 日历事件

- `calendar_list_events(start=None, end=None, search=None, limit=20)`
  - 查询日历事件（可选时间范围；若不传时间范围，默认返回未来 30 天）
- `calendar_get_event(event_id, calendar_id=None)`
  - 按 ID 读取单个日历事件详情（可选指定日历）
- `calendar_create_event(subject, start, end, attendees=None, description=None, location=None, is_all_day=False, time_zone=None, calendar_id=None)`
  - 创建日历事件（支持时间、参会人、描述、地点、全天事件、时区，可选指定日历；未指定 `time_zone` 时默认使用当前用户时区）
- `calendar_update_event(event_id, subject=None, start=None, end=None, attendees=None, description=None, location=None, is_all_day=None, time_zone=None, calendar_id=None)`
  - 更新日历事件（支持改时间、参会人、描述、地点、全天设置等；未指定 `time_zone` 时默认使用当前用户时区）
- `calendar_delete_event(event_id, calendar_id=None)`
  - 删除日历事件
- `calendar_respond_invitation(event_id, response, comment=None, send_response=True, calendar_id=None)`
  - 响应会议邀请（`accept` / `decline` / `tentative`）

### 3.5 用户与租户信息

- `mailbox_list_tenant_users(search=None, limit=20)`
  - 查询租户内用户与邮箱（`displayName` / `mail` / `userPrincipalName`）
- `mailbox_get_user_time_zone()`
  - 获取当前用户邮箱时区（优先返回 `mailboxSettings.timeZone`，失败时回退 `UTC`）

### 3.6 定时发送队列

- `mailbox_create_email_draft_send_job(draft_email_id, schedule_send_time, subject=None, status="scheduled", sent_time=None)`
  - 往 Azure Table `EmailSendQueue` 插入定时发送任务（`draftemailid`、`schedulesendtime`、`status`、`senttime`、`subject`、`userupn`）
  - `schedule_send_time` 需传带时区偏移的 ISO 8601 时间（例如 `2026-07-05T06:00:00Z` 或 `2026-07-05T14:00:00+08:00`），服务端会统一转换为 UTC 后入库

## 4. 基本操作方法（开发流程）

1. 启动 MCP 服务（`mail-mcp`）
2. 确保 MCP Host 调用时携带可用的 Graph Bearer Token，或设置 `OUTLOOK_ACCESS_TOKEN`
3. 在 MCP Host 中连接该服务
  - 本地直连：`http://127.0.0.1:8000/mcp`
  - 经反向代理：`https://mcp.example.com/mcp`
4. 调用 `mailbox_list_folders` 查看目录
5. 调用 `mailbox_list_messages` 查看 `inbox`
6. 调用 `mailbox_get_message` 查看指定邮件正文
7. 调用 `mailbox_compose` 写邮件草稿
8. 回复场景优先调用 `mailbox_reply_compose`（可保留会话上下文）
9. 调用 `mailbox_update_draft` 可修改已创建草稿
10. 调用 `mailbox_revoke_draft` 可撤销（删除）草稿
11. 调用 `mailbox_send_draft` 完成发送

## 5. Copilot Studio 接入使用

在 Copilot Studio 添加 MCP Server 时，建议使用 `OAuth 2.0 -> Manual`。

### 5.1 在 Copilot Studio 中添加 MCP Server

1. 打开 Agent 的 Tools，选择 Add a Model Context Protocol server。
2. 填写基础信息：
  - Server name：自定义名称（例如 `Mail MCP`）
  - Server description：服务说明
  - Server URL：你的 MCP 对外可访问地址（例如 `https://mcp.example.com/mcp`）
3. Authentication 选择 `OAuth 2.0`。
4. Type 选择 `Manual`。

### 5.2 OAuth 2.0 (Manual) 关键字段

- Client ID：填写 Entra 应用的 Service Principal 对应的应用（客户端）ID
- Client secret：填写该应用的密钥
- Redirect URL：填写 Copilot Studio 给出的回调地址，并确保在 Entra 应用中已配置相同 Redirect URI
- Authorization URL：`https://login.microsoftonline.com/{tenant-id}/oauth2/v2.0/authorize`
- Token URL：`https://login.microsoftonline.com/{tenant-id}/oauth2/v2.0/token`

注意：`{tenant-id}` 必须使用实际租户 ID（GUID）或明确租户域，不要使用 `common`。

### 5.3 建议的权限与 Scope

- Scope 建议至少包含：`offline_access openid profile User.ReadBasic.All Mail.Read Mail.ReadWrite Mail.Send Calendars.Read Calendars.ReadWrite MailboxSettings.Read`
- 如果只读场景，可仅保留 `Mail.Read`
- 这些权限需在 Entra 应用中完成授权（必要时管理员同意）

### 5.4 连通性与安全建议

- MCP Server URL 必须是 Copilot Studio 可访问的 HTTPS 公网地址
- 如果服务部署在内网，请通过反向代理暴露 HTTPS 入口
- 建议使用独立应用注册给该 MCP 服务，便于审计和密钥轮换
