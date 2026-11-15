# 架构说明

## 1. 目标与边界

标讯锚是一条单进程、本地优先的批处理数据管线。它只访问由配置声明的 loopback mock 门户，输出限定在所选数据目录。系统强调四件事：

1. **可解释**：每个可预期失败都有稳定原因码。
2. **可追溯**：原始 HTML、响应哈希、字段来源、版本、附件哈希、运行台账、审计事件均有落点。
3. **可重复**：同输入不产生重复公告、版本、附件或复核项；查询、报告和导出顺序稳定。
4. **可复核**：人工通过只读界面查看采集结果和异常，不通过界面修改业务数据。

系统不承担真实采购业务，也不承担通用爬虫、浏览器自动化或分布式采集职责。

## 2. 组件视图

```text
CLI (__main__.py)
  ├─ demo / crawl ───────────────► pipeline.run_crawl
  ├─ serve-portal ──────────────► mock_portal.app
  ├─ serve-review ──────────────► web.create_review_app
  └─ export ────────────────────► export.export_all

pipeline.run_crawl
  ├─ config.load_config / load_rules
  ├─ fetch.SafeFetcher ────────► loopback policy + request
  ├─ extract.parse_list_page
  ├─ extract.extract_notice
  ├─ normalize.parse_amount / parse_*_at
  ├─ attachments.AttachmentStore
  ├─ store.Store ──────────────► SQLite
  ├─ rules.apply_rules
  └─ export.export_all / write_run_report
```

### 模块职责

| 模块 | 职责 |
|---|---|
| `app_paths.py` | 数据目录布局、路径边界、原子写入、运行 ID |
| `config.py` | YAML 载入、loopback 校验、选择器语法校验、规则参数 |
| `errors.py` | 稳定错误类型与原因码目录 |
| `fetch.py` | URL 解析、loopback 白名单、禁止重定向、响应大小/编码校验、审计回调 |
| `extract.py` | 基于配置选择器抽取列表与详情；不负责网络和写入 |
| `normalize.py` | 金额、日期时间、Asia/Shanghai ISO、规范化文本与哈希 |
| `attachments.py` | 附件下载、安全文件名、大小限制、内容哈希、本地路径 |
| `models.py` | 入库记录与运行摘要数据结构 |
| `schema.py` | SQLite DDL 与索引 |
| `store.py` | 写入：运行、公告、版本、原始快照、附件、更正、复核状态、审计 |
| `store_queries.py` | 查询与只读连接；供复核台、导出、测试使用 |
| `rules.py` | 领域质量规则；输入只有规范化记录，不发起网络 |
| `export.py` | JSON/CSV 导出、运行报告、固定排序 |
| `pipeline.py` | 一次采集的生命周期、事务边界、失败收尾 |
| `pipeline_helpers.py` | 分页、字段归一化、来源构造、单公告处理 |
| `web.py` | 只读 Flask 路由、安全响应头、下载 |
| `review_views.py` | 复核台 HTML 构造（无外部资源） |
| `mock_portal/` | 虚构门户；分页、详情、附件、健康检查 |

## 3. 一次运行的时序

1. 读取并校验配置与规则。任何非 loopback 的 `base_url` 或 `allowed_hosts` 在启动前失败。
2. 创建数据目录和 SQLite 连接；写入 `runs` 起始记录。
3. 从 `/notices?page=1&page_size=N` 开始抓取列表。
4. 每页：
   - `SafeFetcher` 在建立连接前校验 URL。
   - 原始列表 HTML 以 SHA-256 前缀命名写入 `raw/list/`。
   - 解析条目与下一页链接；按公告编号去重。
5. 所有条目按 `notice_id` 排序后逐个处理：
   - 抓取详情并写入 `raw/<notice_id>/<sha>.html`。
   - 抽取原始值；调用归一化函数，失败则记录运行错误但继续。
   - 下载附件；失败记录为附件行和复核候选，不中断整批。
   - 在同一事务内 upsert 公告、原始快照记录、附件、更正关系和字段来源。
6. 对本次发现的成功公告执行质量规则。
7. 将当前复核项同步为 `open`；之前存在但本次不再触发的项标记为 `resolved`。
8. 写入运行汇总、错误列表；生成 JSON/CSV 与 `reports/run-*.json`。
9. 关闭 HTTP 会话和 SQLite 连接。

单条公告的抽取或附件失败不阻断其余公告。列表、策略、数据库等不可恢复错误将整次运行标记为 `failed`。

## 4. 幂等与版本

### 公告

以 `notice_id` 为主键。比较详情响应的 `source_hash`：

- 第一次见到：插入公告，`revision=1`，写入一条 `notice_revisions`。
- 相同哈希：只更新 `last_seen_run` 与 `updated_at`，不新增版本，不重写字段来源。
- 哈希变化：更新规范字段，`revision += 1`，追加新的版本记录和当前字段来源。

### 原始 HTML

`raw_snapshots` 以 `(notice_id, sha256)` 唯一。文件按内容哈希落盘，相同内容重复运行不会新增快照行或文件。列表页原始文件也会保留，但由于列表不是单条公告的版本，仅通过审计事件记录路径。

### 附件

以 `(notice_id, source_url)` 唯一。每次运行重新获取并比较内容；本地文件按内容哈希命名，因此相同内容不复制，内容变化会产生新文件并更新行指针。失败行保留稳定原因码，下一次运行会重试并更新该行，不产生重复行。

### 复核项

以 `(notice_id, rule_code)` 唯一。当前仍触发的项保持/恢复 `open`；不再触发的项置为 `resolved` 并记录 `resolved_run`。复核项的 `created_run` 保留首次出现运行。

### 运行

每次命令执行都追加一条 `runs`，用于历史台账；重复采集不会增加公告、版本、附件或复核项数量。

## 5. 确定性与排序

- 列表条目按 `notice_id` 排序后处理，避免分页顺序变化影响入库顺序。
- 公告查询按 `notice_id`；附件按 `(filename, source_url)`；字段来源按 `field_name`；复核项按 `(notice_id, rule_code)`。
- JSON 导出使用 `sort_keys=True`、固定缩进、UTF-8、LF 换行。
- CSV 使用固定列顺序和 `\n` 行尾。
- 错误列表按 `(code, target, message)` 排序。
- 时间统一为带 `+08:00` 的 ISO 8601 文本；内部比较使用可解析的 aware datetime。
- 每次运行的时间戳和运行 ID 会变化，这是运行台账元数据；业务数据、记录顺序、导出顺序保持不变。

## 6. 安全与失败边界

- `LoopbackPolicy` 在 `requests` 调用前解析 URL：仅 HTTP/HTTPS，禁止用户名密码，host 必须同时位于白名单且是 loopback。任何被拒绝的地址都会触发审计事件。
- `SafeFetcher` 设置 `allow_redirects=False` 且关闭环境代理继承，避免重定向或代理把请求带出本机。
- 输出使用 `ensure_within` 解析真实路径并校验相对关系；文件名先剥离目录和危险字符。
- 复核台使用 SQLite 只读 URI 与 `PRAGMA query_only=ON`，不提供写路由。
- 页面使用严格 CSP、禁止 iframe、不加载任何外部脚本、样式或字体。

## 7. 可扩展点

- 新公告类型：扩展 `extract.KIND_MAP`、页面元数据与规则；归一化层无需改动。
- 新字段：`extract.META_LABELS`、`ExtractedNotice`、`pipeline_helpers.SOURCE_FIELD_MAP`、`notices` 表与导出列需同步。
- 新规则：在 `rules.apply_rules` 中产生稳定 `rule_code`；复核台与存储自动消费。
- 新导出格式：基于 `StoreQueriesMixin` 的排序查询添加函数，不要绕过路径边界。
- 新门户：仍须是 loopback mock；可用配置调整路径、分页大小和选择器。

## 8. 已知限制

- 单进程、串行采集；不提供并发、任务队列或断点续抓。中断后重跑依靠幂等收敛。
- 只处理 HTML 选择器和普通 HTTP 文件下载；不解析附件全文、不执行 JavaScript、不做 OCR。
- 公告最新更正不会自动回写到原公告的规范字段；关系、变更值及原公告版本分别保留，供复核与下游判断。
- 缺失脏数据（如“详见附件”预算）会保留原始来源并进入复核队列，而不是猜测金额。
