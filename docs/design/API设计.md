# API 设计

> REST API · 前缀 `/api/v1` · OpenAPI 文档由 FastAPI 自动生成于 `/api/v1/openapi.json`

## 1. 通用约定

### 认证

两种凭据,共用 `Authorization: Bearer <token>` 头,按**格式**区分:

| 用途 | 凭据 | 格式 | 生命周期 |
|---|---|---|---|
| 浏览器 | JWT | 三段点分,`eyJ...` | access 30 分钟,refresh 14 天 |
| CI / 脚本 | API Token | 不透明串,前缀 `at_` | 可设过期时间,可撤销 |

用一个头而不是两个,是为了让 CI 脚本和浏览器客户端共用同一套客户端代码。服务端的依赖注入按前缀分派到不同的校验器。

**API Token 只存哈希**。创建时明文返回一次,之后无法再取。平台自身存明文 token 是严重问题 —— 这个 token 能触发测试执行。

**API Token 以某个用户的身份运行**。`api_token.user_id` 决定权限边界,不引入独立的「机器人账号」概念。这让权限模型保持单一。

### 分页

**常规资源用偏移分页**:

```
GET /projects/1/cases?page=1&page_size=20&keyword=login&folder_id=3
→ 200 {"items": [...], "total": 128, "page": 1, "page_size": 20}
```

`page_size` 上限 **100**,超出则钳制到 100 而非报错(报错会让客户端的「一页拉全部」写法直接失败)。

**`run` 列表用游标分页**:

```
GET /projects/1/runs?cursor=eyJ...&limit=20
→ 200 {"items": [...], "next_cursor": "eyJ..."}
```

`run` 是全库增长最快的表,偏移分页在 `OFFSET 100000` 时要扫描并丢弃十万行。执行记录列表只支持「下一页」,不支持跳页 —— 这是有意的取舍。

`run_step_result` 不分页,一次返回该次执行的全部步骤(单次执行通常几百到几千行),超过 2000 行时截断并在响应里标记 `truncated: true`。

### 错误格式

所有错误响应统一:

```json
{
  "error": {
    "code": "case_not_found",
    "message": "用例不存在",
    "details": { "case_id": 123 }
  }
}
```

`code` 是稳定的机器可读标识,`message` 是给人看的中文说明。客户端**按 `code` 分支,不解析 `message`** —— 这条约定必须写进前端规范,否则文案一改客户端就崩。

HTTP 状态码语义:

| 码 | 用途 |
|---|---|
| 400 | 请求格式错误 |
| 401 | 未认证 / 凭据过期 |
| 403 | 已认证但无该项目权限 |
| 404 | 资源不存在(或无权访问 —— 两者不区分,避免探测) |
| 409 | 冲突:重名、状态不允许该操作(如取消已结束的执行) |
| 422 | 请求格式正确但业务校验失败(FastAPI 默认的校验错误码) |
| 429 | 限流 |

**404 与 403 的区分要克制**。对于不属于当前用户的项目,返回 404 而非 403 —— 否则可以通过「403 还是 404」探测出哪些项目 ID 存在。

### 幂等

`POST /runs` 支持 `Idempotency-Key` 头:

```
POST /api/v1/projects/1/runs
Idempotency-Key: ci-build-1234-attempt-2
```

同一 key 在 24 小时内重复请求返回首次的 `run_id`,不重复创建执行。CI 流水线重试是常态,没有这个机制会产生重复执行记录。

实现:Redis `SETNX idem:{key} {run_id} EX 86400`。

### 版本化

路径前缀 `/api/v1`。破坏性变更升 `/api/v2`,**不做 header 内容协商**。同时维护两个版本的成本远高于让客户端改一次路径。

新增可选字段、新增枚举取值不算破坏性变更,不需要升版本。

## 2. 权限矩阵

角色: `owner` > `admin` > `editor` > `viewer`。

| 操作 | owner | admin | editor | viewer |
|---|:--:|:--:|:--:|:--:|
| 查看所有资源 | ✅ | ✅ | ✅ | ✅ |
| 创建/编辑用例、计划、环境 | ✅ | ✅ | ✅ | — |
| 触发执行 | ✅ | ✅ | ✅ | — |
| 取消他人触发的执行 | ✅ | ✅ | — | — |
| 删除资源 | ✅ | ✅ | — | — |
| 管理成员 | ✅ | ✅ | — | — |
| 管理 API Token | ✅ | ✅ | — | — |
| 管理通知渠道 | ✅ | ✅ | — | — |
| 删除项目 / 转移所有权 | ✅ | — | — | — |

**「触发执行」给到 editor 而不只是 admin** 是刻意的:测试人员的日常工作就是跑测试,把执行权限卡在 admin 会让平台无法使用。

**「取消他人触发的执行」限制到 admin**:取消是破坏性操作,会让他人的测试结果不完整。

实现:`app/api/deps.py` 提供 `require_project_role(min_role)`,作为 FastAPI 依赖注入使用。**所有涉及项目资源的端点都必须挂这个依赖**,不允许「先查出资源再判断归属」的写法。

## 3. 资源清单

```
auth
  POST   /auth/login                       登录,返回 JWT
  POST   /auth/refresh                     刷新 access token
  POST   /auth/logout
  GET    /auth/me                          当前用户与所属项目

projects
  GET    /projects                         我可见的项目列表
  POST   /projects
  GET    /projects/{pid}
  PATCH  /projects/{pid}
  DELETE /projects/{pid}
  GET    /projects/{pid}/members
  POST   /projects/{pid}/members
  PATCH  /projects/{pid}/members/{uid}     改角色
  DELETE /projects/{pid}/members/{uid}

folders
  GET    /projects/{pid}/folders?scope=case
  POST   /projects/{pid}/folders
  PATCH  /folders/{fid}                    改名 / 移动 / 排序
  DELETE /folders/{fid}

environments
  GET    /projects/{pid}/environments
  POST   /projects/{pid}/environments
  GET    /environments/{eid}
  PATCH  /environments/{eid}
  DELETE /environments/{eid}
  GET    /environments/{eid}/variables
  PUT    /environments/{eid}/variables     整体替换

variables                                 项目级变量
  GET    /projects/{pid}/variables
  PUT    /projects/{pid}/variables

api-definitions
  GET    /projects/{pid}/api-definitions
  POST   /projects/{pid}/api-definitions
  GET    /api-definitions/{aid}
  PATCH  /api-definitions/{aid}
  DELETE /api-definitions/{aid}
  POST   /api-definitions/{aid}/publish    生成新版本快照
  GET    /api-definitions/{aid}/versions
  POST   /projects/{pid}/api-definitions/import    OpenAPI / Postman / HAR
  GET    /projects/{pid}/api-definitions/export

cases
  GET    /projects/{pid}/cases
  POST   /projects/{pid}/cases
  GET    /cases/{cid}
  PATCH  /cases/{cid}
  DELETE /cases/{cid}
  POST   /cases/{cid}/publish
  GET    /cases/{cid}/versions
  GET    /cases/{cid}/versions/{v}
  POST   /cases/{cid}/debug                单用例调试执行

plans
  GET    /projects/{pid}/plans
  POST   /projects/{pid}/plans
  GET    /plans/{plid}
  PATCH  /plans/{plid}
  DELETE /plans/{plid}
  GET    /plans/{plid}/items
  PUT    /plans/{plid}/items               整体替换

runs
  POST   /projects/{pid}/runs              触发执行
  GET    /projects/{pid}/runs              列表(游标分页)
  GET    /runs/{rid}
  GET    /runs/{rid}/steps                 步骤结果
  GET    /runs/{rid}/events                ★ SSE 实时进度
  POST   /runs/{rid}/cancel
  POST   /runs/{rid}/rerun
  POST   /runs/{rid}/share                 生成分享链接
  GET    /runs/{rid}/export?format=junit   JUnit XML / HTML

schedules
  GET    /projects/{pid}/schedules
  POST   /projects/{pid}/schedules
  PATCH  /schedules/{sid}
  DELETE /schedules/{sid}
  POST   /schedules/{sid}/enable
  POST   /schedules/{sid}/disable

notification-channels
  GET/POST  /projects/{pid}/notification-channels
  PATCH/DELETE /notification-channels/{nid}
  POST   /notification-channels/{nid}/test 发送测试消息

notification-rules
  GET/POST  /projects/{pid}/notification-rules
  PATCH/DELETE /notification-rules/{nrid}

mock-rules
  GET/POST  /projects/{pid}/mock-rules
  PATCH/DELETE /mock-rules/{mid}
  GET    /projects/{pid}/mock-logs

api-tokens
  GET/POST  /projects/{pid}/api-tokens
  DELETE /api-tokens/{tid}

audit-logs
  GET    /projects/{pid}/audit-logs
```

**Mock 流量端点不在 `/api/v1` 下**。见 §6。

## 4. 执行触发

### `POST /projects/{pid}/runs`

```json
{
  "plan_id": 12,
  "environment_id": 5,
  "case_version": null,
  "parameters_override": { "username": ["alice"] },
  "callback_url": "https://ci.example.com/hooks/test-done",
  "trigger_mode": "ci"
}
```

`plan_id` 与 `case_id` 二选一(单用例调试执行走 `/cases/{cid}/debug`,但 CI 场景下用同一个端点更方便)。

响应 **202 Accepted**:

```json
{ "run_id": "0192f3a4-...", "status": "pending" }
```

用 202 而不是 201:资源已创建但执行尚未完成。`Location` 头指向 `/api/v1/runs/{rid}`。

**为什么不在请求里等结果**:一次计划执行可能几分钟到几十分钟,HTTP 连接扛不住。要同步等待的场景用 `callback_url`。

### `callback_url` 回调

执行进入终态时,平台 POST 到该地址:

```json
{
  "run_id": "0192f3a4-...",
  "status": "failed",
  "summary": { "total": 42, "passed": 40, "failed": 2 },
  "report_url": "https://platform.example.com/runs/0192f3a4-..."
}
```

**回调失败要重试**(最多 3 次,指数退避),并记录到 `notification_log`。回调是 CI 集成的关键路径,静默失败会让流水线永远卡在等待。

**`callback_url` 必须校验**:只允许 http/https,禁止内网地址段(`127.0.0.0/8`、`10.0.0.0/8`、`172.16.0.0/12`、`192.168.0.0/16`、`169.254.0.0/16`)。不做校验的话,这是一个**服务端请求伪造(SSRF)入口** —— 攻击者可以借平台去探测内网。

### `POST /runs/{rid}/cancel`

无请求体。响应:

```json
{ "run_id": "...", "status": "stopping" }
```

**返回 `stopping` 而非 `stopped`**。取消是异步的,worker 在下一个协作点才会停止。客户端应继续监听 SSE 直到收到 `run.finished` 且 `status: stopped`。

若执行已处于终态,返回 **409**,`code: run_already_finished`。

## 5. 实时进度(SSE)

### `GET /runs/{rid}/events`

```
Content-Type: text/event-stream
Cache-Control: no-cache
X-Accel-Buffering: no
```

事件流:

```
id: 1759234567890-0
event: run.started
data: {"total_steps": 42, "started_at": "2026-10-17T10:00:00Z"}

id: 1759234568000-1
event: step.finished
data: {"step_index": 0, "step_name": "登录", "status": "passed", "duration_ms": 120}

id: 1759234570000-2
event: run.progress
data: {"completed": 10, "total": 42}

id: 1759234600000-3
event: run.finished
data: {"status": "success", "summary": {...}}
```

**事件类型**:

| `event` | 触发时机 | 载荷要点 |
|---|---|---|
| `run.started` | 执行开始 | 步骤总数 |
| `step.started` | 步骤开始 | 序号、名称、类型 |
| `step.finished` | 步骤结束 | 状态、耗时、断言统计 |
| `run.progress` | 进度变化(**限流 1/s**) | 已完成 / 总数 |
| `run.finished` | 进入终态 | 终态、汇总 |

`step.started` 让 UI 能显示「正在执行第 N 步」而不是只在完成后跳数字。代价是事件量翻倍 —— 若成为负担,可通过查询参数 `?events=finished` 只订阅完成事件。

### 断线续传

SSE 的 `id` 字段是 **Redis Stream 的消息 ID**。浏览器自动重连时会带 `Last-Event-ID` 头,服务端从该 ID 之后继续读,不丢事件。

这是选 Redis Streams 而非 Pub/Sub 的直接原因。Pub/Sub 是至多一次投递,用户刷新页面时正在执行的那次就再也接不上了。

**若 `Last-Event-ID` 早于 Stream 的保留范围**(Stream 用 `MAXLEN ~ 10000` 裁剪),服务端发送一个 `run.snapshot` 事件,携带从 PG 读出的当前完整状态,然后继续推送后续事件。客户端只需要处理这一种「重新同步」情况。

### 保活

每 **15 秒**发一个注释行(`: keep-alive`)防止代理断连。FastAPI 的 `EventSourceResponse` 已内置此行为。

### 降级

前端在 SSE 连接失败 3 次后,退化为每 2 秒轮询 `GET /runs/{rid}`。该端点本就存在,不需要为降级额外实现任何东西。

## 6. Mock Server 端点

Mock 流量走**独立的路径前缀和独立的服务进程**:

```
ANY  /mock/{project_token}/{path:path}
```

`project_token` 是项目的 Mock 访问令牌(独立于 API Token),可随时重置。

**为什么不用 `/api/v1`**:Mock 端点的消费者是被测系统本身,不是平台用户。它需要:不同的认证方式(或无需认证)、独立的限流、独立的 CORS 策略、独立的日志保留期。混在 `/api/v1` 下会让这些策略无法独立配置。

Mock 请求的响应完全由 `mock_rule` 决定,不返回平台的标准错误格式 —— 它必须看起来像一个真实的第三方接口。

未命中任何规则时返回 **404 + 可配置的兜底响应体**,而不是平台错误格式。同时记入 `mock_request_log`(`matched: false`),这是排查「为什么 Mock 没生效」的唯一依据。

**限流独立配置**。Mock 端点通常承受远高于主 API 的 QPS,且需要防止被测系统的重试风暴打垮平台。

## 7. 导入导出

### 导入

```
POST /projects/{pid}/api-definitions/import
Content-Type: multipart/form-data

file: <二进制>
format: openapi | postman | har     (可选,不传则按内容嗅探)
folder_id: 3
```

**导入是异步的**。一个 OpenAPI 文件可能包含上千个接口,同步解析会让请求超时。响应 202 + 一个导入任务 ID,通过 `GET /import-tasks/{tid}` 查询进度。

导入结果需要**冲突策略**:同名接口已存在时是覆盖、跳过还是新建副本。请求体里传 `on_conflict: skip | overwrite | duplicate`,默认 `skip`。

### 导出

```
GET /projects/{pid}/api-definitions/export?format=openapi
GET /runs/{rid}/export?format=junit|html
```

JUnit XML 是 CI 集成的通用格式 —— Jenkins、GitLab CI、GitHub Actions 都能直接消费。**这是 CI 集成里比 `callback_url` 更通用的路径**:很多 CI 系统只需要一个能解析的测试报告文件。

## 8. 限流

| 端点 | 限制 |
|---|---|
| `POST /auth/login` | 5 次/分钟/IP,失败 10 次锁定 15 分钟 |
| `POST /runs` | 30 次/分钟/用户 |
| `POST /cases/{cid}/debug` | 60 次/分钟/用户 |
| Mock 端点 | 项目级可配,默认 1000 QPS |

**登录限流必须按 IP 而非用户**。按用户限流对撞库攻击无效 —— 攻击者正是用不同的用户名试。

**执行触发限流按用户**:防止一个脚本失控刷出上千次执行,把 worker 队列打满,影响其他所有人。

限流用 Redis 滑动窗口实现。超限返回 **429** + `Retry-After` 头。

## 9. OpenAPI 与客户端

FastAPI 自动生成 OpenAPI 3.1 文档。两条要求:

1. **每个端点必须显式声明 `response_model` 和 `status_code`**,不依赖默认值。否则生成的文档里响应结构是空的,前端无法据此生成类型。
2. **错误响应要写进 `responses` 参数**。否则 OpenAPI 文档里只有成功路径,前端得手工猜错误结构。

前端类型用 `openapi-typescript` 从 `/api/v1/openapi.json` 生成,纳入 CI 检查 —— 后端改了响应结构而前端未同步时,CI 应失败。这比人工同步可靠得多。

## 10. 待定

- **WebSocket 是否必要**。当前所有实时需求都是单向的(服务端推进度),SSE 足够。若日后需要「从浏览器直接控制正在执行的请求」(如跳过某步),才需要 WebSocket。不要为了对称性提前引入。
- **GraphQL 是否必要**。Hoppscotch 用 GraphQL,但它的场景(客户端驱动的数据同步)与平台(服务端执行的资源管理)不同。REST 更合适。若报告页需要一次拉取深度嵌套的数据,再评估。
