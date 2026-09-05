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
az acr build --registry <acr-name> --image mail-mcp:latest .
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

服务会按以下优先级获取 Bearer Token：

1. MCP 请求头中的 `Authorization: Bearer <token>`（推荐）
2. 环境变量 `OUTLOOK_ACCESS_TOKEN`（调试兜底，Graph 与 EWS 均适用）

可选环境变量：

- `MAIL_MCP_BACKEND`（默认 `graph`，可选 `graph` / `ews`；决定使用 Microsoft Graph 还是 Exchange Server EWS）
- `GRAPH_BASE_URL`（默认 `https://graph.microsoft.com/v1.0`）
- `MAIL_ATTACHMENT_SERVICE_HOST`（草稿附件上传/查询服务 host，默认 `https://app-mailattach-dev-6iuhcfhr5qgxo.azurewebsites.net`）
- `DELEGATED_TOKEN_LOG_MODE`（默认 `masked`，可选 `masked` / `full` / `none`）
- `DELEGATED_TOKEN_CACHE_TTL_SECONDS`（默认 `300`，token 校验结果缓存秒数）

服务启动时会固定读取仓库根目录的 `AGENTS.md`，将其作为 MCP 服务级 instructions 随初始化响应提供给客户端，并始终暴露 `mailbox_get_agents_md()` 工具供客户端主动读取。

后端切换示例：

```bash
# 使用 Microsoft Graph（默认）
MAIL_MCP_BACKEND=graph

# 使用 Exchange Server EWS
MAIL_MCP_BACKEND=ews
```

Exchange Server EWS 后端专用环境变量（仅在 `MAIL_MCP_BACKEND=ews` 时生效；此模式不再兼容用户名/密码认证）：

```bash
# 选择 EWS 后端
MAIL_MCP_BACKEND=ews

# Exchange Server EWS 终结点（例如 EWS 地址）
EXCHANGE_SERVER_URL=https://exchange.example.com/EWS/Exchange.asmx

# Entra ID / OAuth 2.0 认证信息
EXCHANGE_SERVER_CLIENT_ID=<app-registration-client-id>
EXCHANGE_SERVER_CLIENT_SECRET=<app-registration-client-secret>
EXCHANGE_SERVER_TENANT_ID=<tenant-id>

# 可选：Mailbox 时区，默认 UTC
EXCHANGE_SERVER_TIME_ZONE=Asia/Shanghai
```

注意：

- 该模式已改为 bearer token 认证，不再接受 `EXCHANGE_SERVER_USERNAME` / `EXCHANGE_SERVER_PASSWORD`。
- EWS 也支持统一的 token 解析顺序：当前请求头中的 `Authorization: Bearer <token>` 优先，未提供时回退到 `OUTLOOK_ACCESS_TOKEN`。
- EWS 仅从当前登录用户的 bearer token 中解析邮箱，不支持指定邮箱/共享邮箱。
- `EXCHANGE_SERVER_URL` 必须指向实际的 EWS/Exchange 端点，不要只填域名。

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
- `MAIL_MCP_BACKEND`（`graph` 或 `ews`）
- `GRAPH_BASE_URL`
- `MAIL_ATTACHMENT_SERVICE_HOST`
- `DELEGATED_TOKEN_LOG_MODE`
- `DELEGATED_TOKEN_CACHE_TTL_SECONDS`

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

查询租户用户（Service Principal）所需 Graph 应用权限：

- `User.ReadBasic.All`（Application，最小权限）
- 需要管理员同意（Admin consent）
- 当前实现通过 `ClientSecretCredential` 获取 `https://graph.microsoft.com/.default` token，并调用 `GET /users`

当前实现固定使用 `/me` 路由访问 Outlook 邮箱。

### 2.1.1 OAuth 2.0 Dynamic discovery

服务支持两种接入模式：

- 默认兼容模式：客户端直接发送 `Authorization: Bearer <Graph token>`
- DCR 模式：当 `MCP_OAUTH_DYNAMIC_DISCOVERY_ENABLED=true` 且 `MCP_PUBLIC_BASE_URL` / `MCP_OAUTH_TENANT_ID` / `MCP_OAUTH_CLIENT_ID` / `MCP_OAUTH_CLIENT_SECRET` 完整时，服务会自动暴露 OAuth 元数据与回调端点

启用 DCR 后会提供：

- `/.well-known/oauth-authorization-server`
- `/.well-known/oauth-protected-resource`
- `/register` / `/authorize` / `/token` / `/revoke`
- `/oauth/callback`

运行状态会写入 Azure Table：

- `OAuthClientRegistry`：客户端注册信息
- `OAuthTokenRegistry`：state / code / token / Graph token 映射

注意：

- MCP 访问令牌由本服务签发并校验
- 实际调用 Graph 时使用登录过程中获取的用户委托 token
- 若未启用 DCR，服务保持兼容模式，直接接收 `Authorization: Bearer <Graph token>`

Token 权限至少满足以下之一：

- `Mail.Read` / `Mail.ReadWrite` / `Mail.Send`
- `Calendars.Read` / `Calendars.ReadWrite`
- `MailboxSettings.Read`

默认地址：

- Host: `127.0.0.1`
- Port: `80`
- Path: `/mcp`

#### 2.1.2 重置 VS Code 缓存的动态 Client ID（触发新 client_id）

当浏览器回调出现类似错误时：

```json
{"error":"invalid_request","error_description":"Client ID '<old-client-id>' not found"}
```

通常是 VS Code 侧缓存了旧的 MCP 动态注册信息。可按以下方式重置。

方法一：命令面板清理（推荐）

1. 打开命令面板：`Ctrl+Shift+P`（Windows/Linux）或 `Cmd+Shift+P`（macOS）。
2. 执行：`Authentication: Remove Dynamic Authentication Provider`。
3. 在列表中选择你的 MCP 服务（例如 `mail-assist-mcp-local`）。
4. 确认后，VS Code 会删除该服务对应的动态 `client_id` 和关联认证缓存。
5. 下次重新连接 MCP 服务时，会重新走 OAuth/DCR 流程并生成新的 `client_id`。

方法二：手动清理本地缓存（高级）

1. 完全退出 VS Code。
2. 清理当前用户的 VS Code 缓存目录中与 MCP/OAuth 相关的条目（重点检查 `User/globalStorage` 与 `User/workspaceStorage`）。
3. 重新打开 VS Code，再次连接该 MCP 服务，触发新的动态注册。

说明：

- 你当前仓库的 `.vscode/mcp.json` 仅配置服务器地址，不保存动态 `client_id`。
- 如果服务端也清理了 `OAuthClientRegistry`，而客户端未清理缓存，也会出现同样错误；此时优先执行方法一。

### 2.2 反向代理与 HTTPS

建议把 `mail-mcp` 仅暴露到内网 HTTP，并由 Nginx/Caddy 负责 443 TLS 终结。核心思路是：

- 后端监听 `127.0.0.1:80`
- 反向代理转发 `/mcp` 到后端
- 对外仅提供 HTTPS 地址，例如 `https://mcp.example.com/mcp`

### 2.3 定时发送

服务提供一个批量发送入口：

- `GET /jobs/dispatch`

它会扫描 Azure Table `EmailSendQueue` 中已到期的 `pending` / `scheduled` 任务，并用 Service Principal 调用 Graph 发送对应草稿。成功写入 `sent`，失败写入 `failed` 和 `lasterror`。

## 4. 集成说明

- 本服务通过 `/mcp` 暴露 MCP 工具
- `/mcp` 请求必须带 `Authorization: Bearer <token>`
- 对于 Copilot Studio，可使用 `OAuth 2.0 -> Manual` 方式接入
- 建议使用独立 Entra 应用注册进行隔离，便于审计、权限控制和密钥轮换
- 最小推荐 scope：`offline_access openid profile Mail.Read Mail.ReadWrite Mail.Send Calendars.Read Calendars.ReadWrite MailboxSettings.Read`

## 5. 约束与注意事项

- Graph 默认后端：`MAIL_MCP_BACKEND=graph`
- EWS 后端仅在 `MAIL_MCP_BACKEND=ews` 时启用，并且仅使用 bearer token，不兼容用户名/密码
- EWS 只从当前登录用户的 bearer token 中解析邮箱，不支持指定邮箱或共享邮箱
- `EXCHANGE_SERVER_URL` 必须指向真实 EWS 端点，不要只填域名
- `OUTLOOK_ACCESS_TOKEN` 仅用于调试兜底；生产环境应优先使用请求头中的 token
