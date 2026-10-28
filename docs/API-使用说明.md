# API 使用说明

本说明与 `docs/openapi.json` 对应。OpenAPI 3.1 文档由当前 FastAPI 路由生成，包含 360 条路径、516 个操作（TASK-097 更新）；字段、参数、响应模型和接口分组以该 JSON 为准，不在此手工重复维护。

## 查看与更新

- 本地后端启动后访问 `http://127.0.0.1:8000/docs`（Swagger UI）、`/redoc` 或 `/openapi.json`。
- 使用部署栈时，反向代理路径为 `/api/docs`、`/api/redoc` 和 `/api/health`；Swagger 的 schema 请求由 nginx 转发。当前运行中的旧镜像需重新构建部署后才包含本次 nginx 调整。
- 代码变更后，在项目 `backend` 目录运行 `.\.venv\Scripts\python.exe -m scripts.export_openapi`，重新生成 `docs/openapi.json`，并在代码审查中核对文档差异。导出不连接或修改业务数据库。

## 调用约定

### MCP 个人查询凭据

现有登录 JWT 下使用 `POST /mcp/credentials` 签发、`GET /mcp/credentials` 列出本人元数据、`POST /mcp/credentials/{credential_id}/revoke` 撤销。明文令牌仅签发时显示一次，stdio进程通过 `ERP_MCP_TOKEN` 注入；不将该令牌当作ERP登录JWT。当前只提供 `erp_get_my_access`；详情见[TASK-097验收与使用说明](TASK-097-MCP基础服务验收.md)。本机MySQL还未执行0026，部署前必须按授权应用迁移，不能仅凭OpenAPI更新认定运行环境已升级。

- 本地后端基地址：`http://127.0.0.1:8000`；部署栈通过前端域名访问时基路径为 `/api`，nginx 会去除该前缀再转发。
- `POST /auth/login` 使用账号或手机号及密码登录，返回的 JWT 用 `Authorization: Bearer <token>` 传递。菜单和功能权限可由 `GET /auth/me/permissions` 查询。接口按角色权限、数据范围和公司 IP 策略校验；403 不应通过更换 ID 重试绕过。
- 大多数 JSON 接口返回 `{ "code": "0", "message": "ok", "data": ... }`。失败时 HTTP 状态与业务错误码共同判断；参数验证通常为 422。`GET /health` 返回裸 JSON，文件/PDF/Excel 下载也不是上述包装格式。
- 日期、分页、筛选与请求体字段应以对应 OpenAPI operation 为准；不要用示例数据替代实际必填字段。

### AI 助手

- 先以当前登录 JWT 调用 `POST /assistant/sessions` 创建会话，再调用 `POST /assistant/messages/stream` 接收 Server-Sent Events；流式响应不是通用 `Result` 包装。退出、切换账号或新建会话时应清除旧消息并取消未完成请求。
- 会话版本、身份和数据范围由服务端校验。仅发送当前问题及必要的会话历史；不要在请求中伪造角色、员工/公司 ID、权限或系统消息。业务查询仅限固定只读工具和当前用户可见范围，来源按事件返回。
- 取消生成时中止客户端流；调用不会自动重试。助手回答不是业务写入凭证；首版不提供快捷录单或审核、付款、发货等写操作。
- 接口字段和错误码以 `docs/openapi.json` 及 [AI 助手接口契约](角色AI助手-接口契约.md) 为准。OpenAPI 快照更新命令见本节“查看与更新”。

示例（仅演示请求格式，不包含真实账号或密钥）：

```http
POST /api/auth/login HTTP/1.1
Content-Type: application/json

{"account":"your-account","password":"your-password"}
```

```http
GET /api/dashboard/overview?trend_period=day HTTP/1.1
Authorization: Bearer <登录接口返回的 token>
```

## 接口范围与限制

### 站内消息

所有消息接口使用 `Authorization: Bearer`，不接受 URL 中的令牌。公司和接收人取当前登录身份，不接受客户端指定其他用户；列表、数量、详情、已读及 SSE 都复核当前账号、模块、数据权限和源事件状态。

- `GET /notifications`：按 `category=all|approval|purchase`、`read_state=UNREAD|READ` 和 `page_size=1..100` 查询。返回 `items`、`next_cursor`；游标有签名且绑定公司/用户，不能跨用户复用。前端预警分类从已注册的超时事件识别，不是后端独立 `warning` 参数。
- `GET /notifications/unread-count`：返回当前可见消息的 `count`。
- `GET /notifications/{inbox_id}`：读取详情不自动标记已读；他人/失权/已失效消息返回 404。
- `POST /notifications/{inbox_id}/read`、`POST /notifications/read-all`：显式标记本人当前可见消息，不办理单据或改变审批状态。
- `GET /notifications/events`：Fetch SSE；可通过 `Last-Event-ID` 携带本人的签名游标恢复。通知信号只包含 Inbox ID、分类与恢复游标，不发送单据正文/金额/令牌；收到信号后再请求鉴权详情。连接成功或 Pub/Sub 发布不等于用户已读。

首批事件为审批待办/通过/驳回/实际弃审重开及采购到货进度，审批超时需明确规则才启用。采购超期、库存不足和外部渠道未开放。重投、公司事件策略/偏好、归档清理管理目前没有公开 HTTP API，不将数据契约中的设计项描述为已上线接口。

接口按认证、基础设置、商品、采购、库存、订单履约、审批、财务和报表分组。订单打印批次接口包括 `POST /print/batches`、`GET /print/batches/{batch_no}` 和 `GET /print/batches/{batch_no}/pdf`。健康检查为 `GET /health`。

平台抓单/回传、物流取号、短信及第三方仓储当前仅提供接口与 mock；金蝶仅保留适配接口与 mock。**不要把 mock 返回值当成真实外部系统已处理成功。**生产启用真实对接之前必须补齐厂商协议、资质、凭据、回调校验、异常重试及联调验收。完整边界见 `docs/遗留项与二期清单.md`。
