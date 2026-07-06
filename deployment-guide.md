# mail-mcp 部署文档

本文档描述如何将本项目部署到 Azure App Service (Linux 容器)，并在 Copilot Studio 中创建 Agent（AI Email Assistant）。

## 0. 前置准备

请先确保：

- 已安装并登录 Azure CLI
  - az login
- 当前订阅正确
  - az account set --subscription <SUBSCRIPTION_ID_OR_NAME>
- 项目根目录中已有可用 Dockerfile（本仓库已提供）
- 你有创建 ACR、App Service Plan、Web App 的权限
- 你有 Copilot Studio 创建 Agent 的权限

建议先准备以下变量（PowerShell 示例）：

```powershell
$env:RG="rg-mail-mcp-prod"
$env:LOC="eastasia"
$env:ACR="acrmailsvcprod001"          # 全局唯一，小写字母数字

$env:PLAN="asp-mail-mcp-linux"
$env:APP="app-mail-mcp-prod-001"      # 全局唯一
$env:IMAGE="mail-mcp"
$env:TAG="v1"

$env:APPREG_NAME="mail-mcp-api"
$env:SP_NAME="sp-mail-mcp-jobs"
$env:STORAGE="stmailmcpprod001"       # 全局唯一，小写字母数字
```

如果资源组不存在，先创建：

`az group create --name $env:RG --location $env:LOC`

## 1. 利用 az acr 构建镜像

在项目根目录执行（使用 ACR 云端构建，不依赖本地 Docker）：

1. 创建 ACR（若已存在可跳过）

    `az acr create --resource-group $env:RG --name $env:ACR --sku Basic`

2. 使用 ACR Build 构建并推送镜像

    `az acr build --registry $env:ACR --image "$($env:IMAGE):$($env:TAG)" .`

3. 验证镜像标签

    `az acr repository show-tags --name $env:ACR --repository $env:IMAGE --output table`

镜像地址示例：

`$env:ACR.azurecr.io/$env:IMAGE:$env:TAG`

根据本文前面变量，真实拼接后的镜像 URL 为：

`acrmailsvcprod001.azurecr.io/mail-mcp:v1`

## 2. 创建 Azure App Service（Container + Linux）

1. 创建 Linux App Service Plan

    `az appservice plan create --name $env:PLAN --resource-group $env:RG --is-linux --sku B1`

2. 创建 Web App（Linux 容器）

    - 说明：这里先用占位镜像创建 Web App，下一步再切换到 ACR 私有镜像。
    `az webapp create --name $env:APP --resource-group $env:RG --plan $env:PLAN --deployment-container-image-name "mcr.microsoft.com/azuredocs/aci-helloworld"`

## 3. 为 App Service 选择镜像并部署

推荐使用托管身份拉取 ACR 镜像（避免明文密码）。

1. 为 Web App 开启系统分配托管身份

    `$principalId = az webapp identity assign --name $env:APP --resource-group $env:RG --query principalId -o tsv`

2. 获取 ACR 资源 ID

    `$acrId = az acr show --name $env:ACR --resource-group $env:RG --query id -o tsv`

3. 授予 Web App 托管身份 AcrPull 权限

    `az role assignment create --assignee-object-id $principalId --assignee-principal-type ServicePrincipal --role AcrPull --scope $acrId`

4. 配置 Web App 使用托管身份拉取 ACR

    `az webapp config set --name $env:APP --resource-group $env:RG --generic-configurations '{"acrUseManagedIdentityCreds": true}'`

5. 配置容器镜像

    `az webapp config container set --name $env:APP --resource-group $env:RG --container-image-name "$($env:ACR).azurecr.io/$($env:IMAGE):$($env:TAG)"`

    - 根据本文前面变量，实际使用的镜像地址为：
    `acrmailsvcprod001.azurecr.io/mail-mcp:v1`

6. 设置应用配置（按需填写）

    - 本项目容器默认监听 80 端口。建议至少配置以下环境变量：
    `az webapp config appsettings set --name $env:APP --resource-group $env:RG --settings MCP_HOST=0.0.0.0 MCP_PORT=80 MCP_PATH=/mcp`

    - 如需 Outlook/Graph 与定时发送能力，还需补充（示例）：
    `az webapp config appsettings set --name $env:APP --resource-group $env:RG --settings GRAPH_BASE_URL=https://graph.microsoft.com/v1.0 DELEGATED_TOKEN_VALIDATE=true`

    - 以及 Azure Table 相关：
        ```shell
        - AZURE_STORAGE_ACCOUNT_NAME
        - AZURE_STORAGE_TABLE_NAME（可选，默认 EmailSendQueue）
        - AZURE_TENANT_ID
        - AZURE_CLIENT_ID
        - AZURE_CLIENT_SECRET
        ```

7. 重启并验证

    `az webapp restart --name $env:APP --resource-group $env:RG`

    访问：
    - https://<APP_NAME>.azurewebsites.net/
    - https://<APP_NAME>.azurewebsites.net/healthz
    - https://<APP_NAME>.azurewebsites.net/mcp

    - 根据本文前面变量，MCP 实际访问地址为：
    `https://app-mail-mcp-prod-001.azurewebsites.net/mcp`

查看实时日志（排障用）：

```shell
az webapp log config --name $env:APP --resource-group $env:RG --application-logging filesystem --level information
az webapp log tail --name $env:APP --resource-group $env:RG
```

## 4. 创建 Service Principal、配置 Scope，并授予 Azure Storage RBAC

本节包含两类身份配置：

- 给 Copilot Studio MCP Server 使用的应用注册与 OAuth Scope
- 给定时发送和 Azure Table 访问使用的 Service Principal 与 Storage RBAC

1. 创建应用注册（App Registration）

    `az ad app create --display-name $env:APPREG_NAME --sign-in-audience AzureADMyOrg`

    创建后，记录以下信息：
    - Application (client) ID，后续用于文档中的 `<app-id>`
    - Directory (tenant) ID，后续用于文档中的 `<tenant-id>`

    也可以通过以下命令查询：

    `$appId = az ad app list --display-name $env:APPREG_NAME --query "[0].appId" -o tsv`
    `$tenantId = az account show --query tenantId -o tsv`

2. 为应用注册创建 Service Principal

    `az ad sp create --id $appId`

3. 为应用注册配置 Identifier URI

    `az ad app update --id $appId --identifier-uris "api://$appId"`

4. 暴露 OAuth Scope：`user_impersonation`

    建议在 Azure Portal 中完成，步骤更直接：
    - 进入 Microsoft Entra ID
    - 打开 App registrations
    - 选择 `$env:APPREG_NAME`
    - 进入 Expose an API
    - 确认 Application ID URI 为 `api://<app-id>`
    - 选择 Add a scope
    - Scope name 填写 `user_impersonation`
    - Admin consent display name 可填写 `Access mail-mcp API`
    - Admin consent description 可填写 `Allow Copilot Studio to call the mail-mcp API as the signed-in user`
    - State 选择 `Enabled`

    配置完成后，Copilot Studio 中使用的 Scope 为：

    `api://<app-id>/user_impersonation`

5. 创建用于 Azure Table 访问的 Service Principal

    `az ad sp create-for-rbac --name $env:SP_NAME --skip-assignment`

    请保存返回结果中的：
    - `appId`，对应应用配置中的 `AZURE_CLIENT_ID`
    - `password`，对应应用配置中的 `AZURE_CLIENT_SECRET`
    - `tenant`，对应应用配置中的 `AZURE_TENANT_ID`

6. 获取 Storage Account 资源 ID

    `$storageId = az storage account show --name $env:STORAGE --resource-group $env:RG --query id -o tsv`

7. 授予 Azure Storage RBAC 权限

    建议最小权限为 `Storage Table Data Contributor`，作用域设置到 Storage Account：

    `$spAppId = az ad sp list --display-name $env:SP_NAME --query "[0].appId" -o tsv`
    `az role assignment create --assignee $spAppId --role "Storage Table Data Contributor" --scope $storageId`

    如果 Flow 还需要把附件写入 Blob Storage，额外授予：

    `az role assignment create --assignee $spAppId --role "Storage Blob Data Contributor" --scope $storageId`

8. 将 Service Principal 配置到 App Service

    `az webapp config appsettings set --name $env:APP --resource-group $env:RG --settings AZURE_STORAGE_ACCOUNT_NAME=$env:STORAGE AZURE_TENANT_ID=<tenant-id> AZURE_CLIENT_ID=<client-id> AZURE_CLIENT_SECRET=<client-secret>`

    其中：
    - `<tenant-id>` 使用上一步 Service Principal 返回的 tenant
    - `<client-id>` 使用上一步 Service Principal 返回的 appId
    - `<client-secret>` 使用上一步 Service Principal 返回的 password

9. 配置 SharePoint（用于存储附件）

    此步骤主要给 Power Automate Flow 提供一个企业内可协作的附件落地点。

    建议涉及以下配置：
    - 在 SharePoint 中准备一个站点，专门用于 AI Email Assistant 附件存储
    - 在该站点中创建或指定一个文档库，例如 `MailAttachments`
    - 为执行 Flow 的账号或连接器授予该站点和文档库的上传权限
    - 在 Power Automate Flow 中添加 SharePoint 连接，指向目标站点和文档库
    - Flow 上传附件后，返回 SharePoint 文件链接，供 Agent 后续写回邮件草稿或会议描述
    - 如有合规要求，可在 SharePoint 侧补充命名规则、目录结构、保留策略和访问控制

## 5. 在 Copilot Studio 中创建 Agent（AI Email Assistant）

1. 进入 Copilot Studio，创建新 Agent
    - 名称：AI Email Assistant

2. 模型设置
    - Model: GPT-5 Chat

3. Instructions 设置
    - 打开仓库根目录的 AGENTS.md
    - 将 AGENTS.md 全部内容复制并粘贴到 Agent 的 Instructions
    - 保存

4. Knowledge 设置（在 Knowledge 中添加以下 3 个链接：）
    - https://learn.microsoft.com/graph/search-query-parameter
    - https://learn.microsoft.com/graph/filter-query-parameter
    - https://learn.microsoft.com/graph/query-parameters

5. 在 Agent 的 Tool 中添加 MCP Server
    - 进入 Agent 的 Tools 配置页
    - 选择添加 MCP Server
    - Server Name 可填写：mail-mcp
    - 在 MCP Server URL 中填写：
    `https://app-mail-mcp-prod-001.azurewebsites.net/mcp`
    - Authorization URL：
    `https://login.microsoftonline.com/<tenant-id>/oauth2/v2.0/authorize`
    - Token URL：
    `https://login.microsoftonline.com/<tenant-id>/oauth2/v2.0/token`
    - Refresh URL：
    `https://login.microsoftonline.com/<tenant-id>/oauth2/v2.0/token`
    - Scope：
    `api://<app-id>/user_impersonation`
    - 其中：
        - `<tenant-id>` 替换为你的 Microsoft Entra Tenant ID
        - `<app-id>` 替换为你为该 MCP Server 配置的应用注册 Application (client) ID
    - 如果后续更换了 App Service 名称，则按同样规则替换为：
    `https://<APP_NAME>.azurewebsites.net/mcp`

6. 添加 Topic：草稿与会议附件变更处理（仅附件触发）
    - 在 Agent 的 Topics 页面新增一个 Topic，名称填写：`草稿与会议附件变更处理（仅附件触发）`
    - Trigger 建议设置为 `By agent`
    - 将该 Topic 启用
    - 增加该 Topic 的目的说明：附件体积通常较大，不适合直接通过 LLM 上下文传输，因此附件处理需要下沉到独立 Topic 和 Power Automate Flow 中执行
    - 在 Topic 中配置 Power Automate Flow，用于接收附件并完成外部存储或分发
    - Flow 的建议处理链路：
        - 上传附件到 Azure Blob Storage，用于大文件持久化存储和生成访问链接
        - 按需同步到 SharePoint，用于企业文档协作、归档和附件链接回写
        - 按需发送到 Email，用于邮件草稿或会议场景中的附件流转
    - Topic 执行完成后，应返回附件名称和可访问链接，例如 `fileName` 与 `fileUrl`
    - 后续由 Agent 或 MCP 工具把附件信息写回邮件草稿正文或会议描述，避免在 LLM 中直接承载大附件内容

7. 发布与验证
    - 发布 Agent
    - 在测试对话中验证以下能力：
        - 邮件查询参数解释（`$search / $filter / query parameters`）
        - 能按照时间范围和关键词给出正确检索建议
        - 不会在缺少关键信息时直接执行发送
        - 上传大附件时，能够通过 Topic + Power Automate Flow 完成 Blob / SharePoint / Email 处理，而不是把附件内容直接塞进 LLM

## 6. 常见问题

1. Web App 拉取镜像失败
    - 检查 Web App 是否已开启托管身份
    - 检查 AcrPull 角色是否授予到正确 ACR 作用域
    - 检查镜像名和标签是否存在

2. 部署成功但健康检查失败
    - 检查应用是否监听 80 端口
    - 检查 MCP_HOST/MCP_PORT/MCP_PATH 是否正确
    - 查看 az webapp log tail 实时日志

3. Copilot Studio 回答与预期不一致
    - 确认 Instructions 已完整粘贴 AGENTS.md 内容
    - 确认 Knowledge 的 3 个链接都已添加且可访问
    - 重新发布后再测试

## 7. 一次性执行命令（可选）

以下命令可按顺序快速执行（需先设置变量）：

```shell
az group create --name $env:RG --location $env:LOC
az acr create --resource-group $env:RG --name $env:ACR --sku Basic
az acr build --registry $env:ACR --image "$($env:IMAGE):$($env:TAG)" .

az appservice plan create --name $env:PLAN --resource-group $env:RG --is-linux --sku B1
az webapp create --name $env:APP --resource-group $env:RG --plan $env:PLAN --deployment-container-image-name "mcr.microsoft.com/azuredocs/aci-helloworld"

$principalId = az webapp identity assign --name $env:APP --resource-group $env:RG --query principalId -o tsv
$acrId = az acr show --name $env:ACR --resource-group $env:RG --query id -o tsv
az role assignment create --assignee-object-id $principalId --assignee-principal-type ServicePrincipal --role AcrPull --scope $acrId

az webapp config set --name $env:APP --resource-group $env:RG --generic-configurations '{"acrUseManagedIdentityCreds": true}'
az webapp config container set --name $env:APP --resource-group $env:RG --container-image-name "$($env:ACR).azurecr.io/$($env:IMAGE):$($env:TAG)"
az webapp config appsettings set --name $env:APP --resource-group $env:RG --settings MCP_HOST=0.0.0.0 MCP_PORT=80 MCP_PATH=/mcp
az webapp restart --name $env:APP --resource-group $env:RG
```
