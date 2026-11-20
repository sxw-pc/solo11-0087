# 数据字典

所有持久化数据位于所选数据目录的 `tender_anchor.sqlite3`。时间文本统一为带 `+08:00` 的 ISO 8601（Asia/Shanghai）；金额为两位小数的十进制文本，不使用浮点数。

## 1. 关系概览

```text
runs 1 ─── n notice_revisions
  │              │
  │              └── notice_id ──► notices
  ├── n audit_events
  └── n review_items ──► notices

notices 1 ─── n attachments
    │   1 ─── n raw_snapshots
    │   1 ─── n field_sources
    │   n ─── n corrections（通过 correction_notice_id / original_notice_id）
    └── 1 ─── n review_items
```

## 2. 表定义

### `schema_meta`

| 字段 | 类型 | 说明 |
|---|---|---|
| `key` | TEXT PK | 元数据键 |
| `value` | TEXT | 当前 `schema_version=1` |

### `runs`

| 字段 | 类型 | 说明 |
|---|---|---|
| `run_id` | TEXT PK | 形如 `run-20261115T113501510239-<config6>` |
| `started_at` / `finished_at` | TEXT | 上海时区 ISO 8601 |
| `status` | TEXT | `running` / `completed` / `completed_with_errors` / `failed` |
| `portal_base` | TEXT | 本次门户地址，只可能是 loopback |
| `config_hash` | TEXT | 配置指纹 SHA-256 |
| `pages_fetched` | INTEGER | 已抓列表页数 |
| `notices_seen` | INTEGER | 本次发现的公告条目数 |
| `notices_inserted` | INTEGER | 新增公告数 |
| `notices_updated` | INTEGER | 版本变化公告数 |
| `notices_unchanged` | INTEGER | 内容未变公告数 |
| `attachments_ok` | INTEGER | 本次成功附件次数 |
| `attachments_failed` | INTEGER | 本次失败附件次数 |
| `review_open` | INTEGER | 本次同步后未处理复核项数 |
| `errors_json` | TEXT | 稳定错误对象数组 JSON |

错误对象字段：`code`、`message`、`target`。

### `notices`

| 字段 | 类型 | 空值 | 说明 |
|---|---|---|---|
| `notice_id` | TEXT PK | 否 | 公告编号 |
| `kind` | TEXT | 否 | `tender` / `correction` / `result` / `other` |
| `kind_label` | TEXT | 否 | 页面原始类型文本 |
| `title` | TEXT | 否 | 标题 |
| `project_code` | TEXT | 否，默认空串 | 项目编号 |
| `region` | TEXT | 否，默认空串 | 地区 |
| `purchaser` | TEXT | 否，默认空串 | 采购单位 |
| `agency` | TEXT | 否，默认空串 | 代理机构 |
| `publish_at` | TEXT | 可空 | 规范化发布时间 |
| `deadline_at` | TEXT | 可空 | 规范化截止时间 |
| `budget_amount` | TEXT | 可空 | 规范化预算金额 |
| `budget_currency` | TEXT | 否 | 固定 `CNY` |
| `award_amount` | TEXT | 可空 | 规范化成交金额 |
| `winner` | TEXT | 否，默认空串 | 成交供应商 |
| `related_notice_id` | TEXT | 否，默认空串 | 更正公告关联的原公告 |
| `content_excerpt` | TEXT | 否 | 正文摘要，最多 600 字符（末尾省略号） |
| `source_url` | TEXT | 否 | 详情页 URL |
| `source_hash` | TEXT | 否 | 详情 HTML 字节的 SHA-256 |
| `first_seen_run` | TEXT | 否 | 首次运行 ID |
| `last_seen_run` | TEXT | 否 | 最近运行 ID |
| `revision` | INTEGER | 否 | 内容版本，从 1 开始 |
| `updated_at` | TEXT | 否 | 行更新时间；内容未变时也会更新 |

### `notice_revisions`

| 字段 | 类型 | 说明 |
|---|---|---|
| `revision_id` | INTEGER PK AUTOINCREMENT | 版本记录序号 |
| `notice_id` | TEXT | 公告编号 |
| `run_id` | TEXT | 产生该版本的运行 |
| `source_hash` | TEXT | 详情 HTML SHA-256 |
| `payload_json` | TEXT | 规范化公告、附件、更正变更、原始来源值的快照 |
| `fetched_at` | TEXT | 抓取时间 |

唯一约束：`(notice_id, source_hash)`。相同内容重复运行不新增版本。

### `raw_snapshots`

| 字段 | 类型 | 说明 |
|---|---|---|
| `snapshot_id` | INTEGER PK | 快照序号 |
| `notice_id` | TEXT | 公告编号 |
| `source_url` | TEXT | 详情 URL |
| `sha256` | TEXT | HTML 内容哈希 |
| `stored_path` | TEXT | 相对数据目录的路径，如 `raw/TA-2026-0001/<hash>.html` |
| `byte_size` | INTEGER | 字节数 |
| `fetched_at` | TEXT | 抓取时间 |

唯一约束：`(notice_id, sha256)`。

### `attachments`

| 字段 | 类型 | 说明 |
|---|---|---|
| `attachment_id` | INTEGER PK | 附件行序号 |
| `notice_id` | TEXT | 所属公告 |
| `filename` | TEXT | 安全文件名（无目录） |
| `source_url` | TEXT | 附件 URL |
| `sha256` | TEXT | 成功时为内容哈希；失败为空 |
| `size_bytes` | INTEGER | 成功时为字节数；失败为 0 |
| `stored_path` | TEXT | 相对数据目录路径；失败为空 |
| `content_type` | TEXT | 响应 MIME 或按扩展名推断 |
| `status` | TEXT | `stored` / `failed` |
| `error_code` | TEXT | 失败原因码，成功为空 |
| `fetched_at` | TEXT | 最近抓取时间 |
| `updated_run` | TEXT | 最近更新运行 |

唯一约束：`(notice_id, source_url)`。

### `corrections`

| 字段 | 类型 | 说明 |
|---|---|---|
| `correction_notice_id` | TEXT PK | 更正公告编号 |
| `original_notice_id` | TEXT | 原公告编号；可能为空并触发规则 |
| `published_at` | TEXT | 更正公告发布时间 |
| `changes_json` | TEXT | 变更项数组 JSON |
| `source_url` | TEXT | 更正公告 URL |
| `first_seen_run` / `last_seen_run` | TEXT | 首次/最近运行 |
| `updated_at` | TEXT | 行更新时间 |

`changes_json` 元素：

```json
{
  "field": "budget_amount",
  "label": "预算金额",
  "raw_value": "1,350,000.00元",
  "normalized_value": "1350000.00"
}
```

### `field_sources`

| 字段 | 类型 | 说明 |
|---|---|---|
| `notice_id` | TEXT | 公告编号 |
| `field_name` | TEXT | 规范字段名 |
| `raw_value` | TEXT | 页面原始值 |
| `normalized_value` | TEXT | 规范化值；无法归一化时为空 |
| `extractor` | TEXT | `css_text` / `meta_table` |
| `selector` | TEXT | 配置选择器或 `选择器 [元数据标签]` |
| `updated_at` | TEXT | 最近更新时间 |

主键：`(notice_id, field_name)`。

### `review_items`

| 字段 | 类型 | 说明 |
|---|---|---|
| `item_id` | INTEGER PK | 复核项序号 |
| `notice_id` | TEXT | 公告编号 |
| `rule_code` | TEXT | 稳定规则码 |
| `severity` | TEXT | `warning` / `error` |
| `message` | TEXT | 人可读说明 |
| `field_name` | TEXT | 相关规范字段，可为空串 |
| `status` | TEXT | `open` / `resolved` |
| `created_run` | TEXT | 首次产生运行 |
| `last_seen_run` | TEXT | 最近触发运行 |
| `resolved_run` | TEXT | 解决运行，未解决为空 |

唯一约束：`(notice_id, rule_code)`。

### `audit_events`

| 字段 | 类型 | 说明 |
|---|---|---|
| `event_id` | INTEGER PK | 事件序号 |
| `run_id` | TEXT | 运行 ID |
| `event_type` | TEXT | `FETCH_BLOCKED` / `FETCH_ERROR` / `LIST_ARCHIVED` |
| `target` | TEXT | URL 或归档路径 |
| `detail` | TEXT | 原因码或归档路径 |
| `created_at` | TEXT | 事件时间 |

外部地址、重定向、HTTP 错误等安全/传输事件会写审计；正常请求的留档由 `raw_snapshots` 和列表归档路径体现。

## 3. 导出 JSON

顶层：

```json
{
  "schema_version": 1,
  "generated_at": "2026-11-15T11:35:08+08:00",
  "notice_count": 8,
  "notices": []
}
```

`notices` 每项字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `notice_id` | string | 公告编号 |
| `kind` / `kind_label` | string | 规范类型与原始类型 |
| `title` | string | 标题 |
| `project_code` / `region` / `purchaser` / `agency` | string | 元数据 |
| `publish_at` / `deadline_at` | string/null | ISO 时间 |
| `budget` | object | `{amount, currency}` |
| `award` | object | `{amount, winner}` |
| `related_notice_id` | string | 关联原公告 |
| `content_excerpt` | string | 正文摘要 |
| `source` | object | `{url, sha256, first_seen_run, last_seen_run, revision}` |
| `attachments` | array | 文件名、URL、哈希、大小、类型、状态、错误码、相对路径 |
| `corrections` | array | 更正公告、原公告、发布时间、变更数组、来源 URL |
| `field_sources` | array | 字段、原始值、规范化值、抽取器、选择器 |
| `quality` | array | 规则码、级别、说明、状态 |

排序：公告按 `notice_id`；附件按 `(filename, source_url)`；更正按 `correction_notice_id`；字段来源按 `field_name`。

## 4. 导出 CSV

固定列：

`notice_id, kind, kind_label, title, project_code, region, purchaser, agency, publish_at, deadline_at, budget_amount, budget_currency, award_amount, winner, revision, source_url, source_sha256, attachments_stored, attachments_failed, quality_codes`

- 行按 `notice_id` 排序。
- `quality_codes` 为去重排序后的规则码，以 `|` 连接。
- 时间与金额保持数据库中的规范文本。

## 5. 附件本地命名

```text
attachments/<notice_id>/<sha256前12位>_<安全文件名>
```

- `<notice_id>` 与文件名均经过安全 basename 处理。
- 相同内容、相同文件名的重复运行不会重复写入。
- 文件名中的目录分隔符、控制字符和 Windows 保留分隔符会被处理。
- 路径在写入前通过 `ensure_within(data_root, ...)` 检查。

## 6. 原始留档

- 列表页：`raw/list/page-<三位页码>-<sha前12位>.html`
- 详情页：`raw/<notice_id>/<sha前16位>.html`
- 文件名中的哈希来自原始响应字节。
- 文件按哈希去重，不覆盖既有历史。

## 7. 跨标投标保证覆盖

跨标保证案件独立于投标信封受理，持久化在同一 SQLite 的五张 `bond_*` 表中。保证工具的名义面额之和从不等于可用保证：可用性由已采集公告、更正字段、投标主体与有效期共同决定，且任何时点占用不得超过工具面额。

### `bond_cases`

| 字段 | 类型 | 说明 |
|---|---|---|
| `case_id` | TEXT PK | 由案件内排序后的 bid id 确定性派生，如 `CASE-001-004` |
| `payload_hash` | TEXT | 案件材料规范化 JSON 的 SHA-256；同 ID 不同内容即冲突 |
| `payload_json` | TEXT | 原始案件材料 |
| `contract_mode` | TEXT | `fixed_pledge` / `preauthorized_reallocation` |
| `created_at` / `updated_at` | TEXT | 上海时区 ISO 8601 |

### `bond_bids`

逐标的持久化信封判定与保证判定。主键 `(case_id, bid_id)`。`security_status` 取值：

- `security_covered`：已质押额度达到 `required_security`。
- `security_shortfall`：仅获得部分自由额度，仍需补保证。
- `pending_security`：无可用工具证据。
- `bid_ineligible`：信封本身被拒（截止/预算）。
- `evidence_pending`：公告或保函证据缺失，待核，不猜测资格。

`decision_json` 记录有效截止时间（含 `correction_sources`）、分配明细与覆盖金额。

### `bond_instruments`

逐工具记录面额、`valid_until`、证据状态与对各标的的可用性。银行保函缺少 `valid_until` 时为 `evidence_pending`；有效期早于标的有效截止时间（取原公告与全部已采集更正中的最晚截止）即被阻断。

### `bond_pledge_ledger`

只追加的占用/释放台账。`event_type` 为 `pledge` 或 `release`，`seq` 在案件内单调。再分配以单事务写入成组台账行：新增质押先于其腾出空间所对应的释放，保证任何中间状态下已提交标的不失保、工具不超面额。`source_action_id` 记录每行的来源动作（如 `intake`）。

### `bond_events`

动作留痕，自增 `event_seq` 保持写入顺序，`(case_id, action_id)` 唯一。相同动作材料重放返回原结果且不新增台账；同一 `action_id` 不同内容冲突报错。

### 两个合同的语义

- **固定质押合同（`fixed_pledge`）**：已正式提交标的的既有质押不可移动，新标的只能使用真正自由的可拆额度。按 bid 顺序先占现金的贪心可能错误宣布联合无解，因此分配使用跨标的整体回溯（`bond_coverage.allocate`），无解时再以 `best_effort_allocate` 显式部分覆盖与差额。
- **预授权再分配合同（`preauthorized_reallocation`）**：必须携带供应商书面授权。规划器（`bond_reallocation.plan_replacement`）原子生成质押/释放序列，逐步校验面额与失保不变量，任一步不可行则整笔动作拒绝且不留痕。

导出为 `exports/bond_coverage.json` 与 `exports/bond_coverage.csv`；复核台 `/bonds` 只读展示案件、工具与台账。

## 8. 稳定错误码

见 `src/tender_anchor/errors.py` 的 `ERROR_CATALOG`，涵盖配置、URL/网络、解析、归一化、附件、存储、导出与管线。运行错误对象只使用这些稳定代码；HTTP 非 2xx 使用 `FETCH_HTTP_ERROR`，具体状态放在错误消息和上下文中。保证业务的材料冲突、授权缺失、超面额与失保等以 `ValueError` 稳定中文消息抛出，由 CLI 统一收口为错误对象。
