# Mail MCP (Python + modelcontextprotocol)

这是一个邮件助手 MCP 服务骨架，基于 Python 和官方 MCP Python SDK（`mcp` 包）。

当前版本先实现：
- MCP 服务入口
- 本地邮箱数据存储（JSON）
- 常见邮件助手基础工具（列目录、读邮件、搜索、写草稿、发草稿）

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

默认地址：
- Host: `127.0.0.1`
- Port: `8000`
- MCP Path: `/mcp`
- 后端 URL: `http://127.0.0.1:8000/mcp`

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
$env:MCP_PORT = "9000"
$env:MCP_PATH = "/mcp"
mail-mcp
```

### 2.1 通过反向代理提供 HTTPS（推荐）

思路：
- `mail-mcp` 只监听内网 HTTP（如 `127.0.0.1:8000`）
- Nginx/Caddy 对外暴露 443，负责证书和 TLS

Nginx 示例：

```nginx
server {
  listen 443 ssl;
  server_name mcp.example.com;

  ssl_certificate     /etc/letsencrypt/live/mcp.example.com/fullchain.pem;
  ssl_certificate_key /etc/letsencrypt/live/mcp.example.com/privkey.pem;

  location /mcp {
    proxy_pass http://127.0.0.1:8000/mcp;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto https;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
  }
}
```

Caddy 示例：

```caddy
mcp.example.com {
  reverse_proxy 127.0.0.1:8000
}
```

> 对外连接地址示例：`https://mcp.example.com/mcp`

## 3. 已提供的工具（Tools）

- `ping()`
  - 健康检查
- `mailbox_list_folders()`
  - 列出文件夹（`inbox` / `drafts` / `sent`）
- `mailbox_list_messages(folder="inbox", limit=20)`
  - 列出邮件摘要
- `mailbox_get_message(message_id)`
  - 按 ID 获取邮件详情
- `mailbox_search(query, folder="inbox", limit=20)`
  - 关键字搜索邮件
- `mailbox_compose(to, subject, body, cc=None, bcc=None)`
  - 生成草稿
- `mailbox_send_draft(draft_id)`
  - 发送草稿并移到 `sent`

## 4. 数据文件

本地数据在：
- `data/mailbox.json`

首次启动会自动初始化示例数据。

## 5. 基本操作方法（开发流程）

1. 启动 MCP 服务（`mail-mcp`）
2. 在 MCP Host 中连接该服务
  - 本地直连：`http://127.0.0.1:8000/mcp`
  - 经反向代理：`https://mcp.example.com/mcp`
3. 调用 `mailbox_list_folders` 查看目录
4. 调用 `mailbox_list_messages` 查看 `inbox`
5. 调用 `mailbox_get_message` 查看指定邮件正文
6. 调用 `mailbox_compose` 写邮件草稿
7. 调用 `mailbox_send_draft` 完成发送

## 6. 下一步扩展建议

- 抽象 `MailStore` 为接口，增加真实邮件提供商适配层：
  - Microsoft Graph（Outlook）
  - Gmail API
  - IMAP/SMTP
- 增加身份认证与令牌管理
- 增加邮件标签、附件、线程会话支持
- 增加安全策略（敏感词、越权校验、审计日志）
