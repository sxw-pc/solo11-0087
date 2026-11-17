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
    │   1 ─── n review_items
    │   1 ─── 0..1 attributions（结果公告 → 招标轮次归属结论）
    └── n ─── n round_candidates（结果 × 招标的候选资格证据）
```

## 2. 表定义

### `schema_meta`

| 字段 | 类型 | 说明 |
|---|---|---|
| `key` | TEXT PK | 元数据键 |
| `value` | TEXT | 当前 `schema_version=2` |

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

### `round_candidates`

每条结果公告 × 每个招标轮次的候选资格证据，推断归属只使用 `eligible=1` 的行。

| 字段 | 类型 | 说明 |
|---|---|---|
| `result_notice_id` | TEXT | 结果公告编号 |
| `tender_notice_id` | TEXT | 招标公告编号 |
| `same_project_code` | INTEGER | 项目编号一致且非空 |
| `same_purchaser` | INTEGER | 采购单位一致且非空 |
| `publish_order_ok` | INTEGER | 招标发布时间不晚于结果发布时间 |
| `corrections_ok` | INTEGER | 该轮次全部更正均不晚于结果发布时间 |
| `eligible` | INTEGER | 以上四项全部为真 |
| `first_seen_run` / `last_seen_run` | TEXT | 首次/最近运行 |

主键：`(result_notice_id, tender_notice_id)`。重复采集不新增行，只更新检查值与 `last_seen_run`。

### `attributions`

每条结果公告一行的归属结论，同时携带两种业务授权口径的状态。

| 字段 | 类型 | 说明 |
|---|---|---|
| `result_notice_id` | TEXT PK | 结果公告编号 |
| `declared_notice_id` | TEXT | 公告自身“关联原公告”引用，可为空 |
| `listed_notice_id` | TEXT | 本地显式归属清单指向，可为空 |
| `inferred_notice_id` | TEXT | 唯一候选推断指向，可为空 |
| `chosen_notice_id` | TEXT | 最终归属招标轮次；未确认为空 |
| `evidence_level` | TEXT | `declared` / `explicit_list` / `inferred` / `none` |
| `status` | TEXT | `confirmed` / `conflict` / `pending` |
| `reason_code` | TEXT | 未确认时的稳定原因码，确认为空 |
| `strict_status` | TEXT | 严格台账口径：`closed` / `pending` |
| `auxiliary_status` | TEXT | 辅助核验口径：`closed` / `pending` |
| `detail` | TEXT | 人可读证据说明；推断明确标注“并非源公告声明” |
| `first_seen_run` / `last_seen_run` / `updated_at` | TEXT | 运行与行更新时间 |

稳定原因码：`RESULT_TENDER_MISSING`（缺原招标）、`RESULT_CANDIDATES_AMBIGUOUS`（多个候选）、`RESULT_BEFORE_TENDER`（结果早于招标）、`ATTRIBUTION_SOURCE_CONFLICT`（清单与公告引用冲突）、`ATTRIBUTION_TARGET_MISMATCH`（显式清单指向不匹配轮次）。`RESULT_SELF_REVISED`（结果公告自身被更正修订）以复核项形式出现在 `review_items`。

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
  "schema_version": 2,
  "generated_at": "2026-11-15T11:35:08+08:00",
  "notice_count": 8,
  "notices": [],
  "rounds": []
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
| `attribution` | object/null | 结果公告的归属证据块；非结果公告为 null |
| `content_excerpt` | string | 正文摘要 |
| `source` | object | `{url, sha256, first_seen_run, last_seen_run, revision}` |
| `attachments` | array | 文件名、URL、哈希、大小、类型、状态、错误码、相对路径 |
| `corrections` | array | 更正公告、原公告、发布时间、变更数组、来源 URL |
| `field_sources` | array | 字段、原始值、规范化值、抽取器、选择器 |
| `quality` | array | 规则码、级别、说明、状态 |

`attribution` 块字段：`status`、`evidence_level`、`tender_notice_id`、`declared_notice_id`、`listed_notice_id`、`inferred_notice_id`、`reason_code`、`strict_status`、`auxiliary_status`、`detail`、`candidates`（每项招标轮次的四项一致性检查与 `eligible`）。

`rounds` 每项字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `tender_notice_id` | string | 招标轮次公告编号 |
| `project_code` / `purchaser` / `publish_at` | string | 轮次元数据 |
| `corrections` | array | 指向该轮次的更正公告编号，升序 |
| `strict` | object | 严格台账结论：`status`、`result_notice_ids`、`evidence_level` |
| `auxiliary` | object | 辅助核验结论，结构同上；额外接受 `inferred` 证据 |

排序：公告按 `notice_id`；轮次按 `tender_notice_id`；附件按 `(filename, source_url)`；更正按 `correction_notice_id`；字段来源按 `field_name`。

## 4. 导出 CSV

固定列：

`notice_id, kind, kind_label, title, project_code, region, purchaser, agency, publish_at, deadline_at, budget_amount, budget_currency, award_amount, winner, revision, source_url, source_sha256, attachments_stored, attachments_failed, quality_codes, attributed_tender_id, attribution_evidence, attribution_status, round_strict_status, round_auxiliary_status`

- 行按 `notice_id` 排序。
- `quality_codes` 为去重排序后的规则码，以 `|` 连接。
- `attributed_tender_id` / `attribution_evidence` / `attribution_status` 仅结果公告有值。
- `round_strict_status` / `round_auxiliary_status` 仅招标公告有值，为 `closed` 或 `pending`。
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

## 7. 稳定错误码

见 `src/tender_anchor/errors.py` 的 `ERROR_CATALOG`，涵盖配置、URL/网络、解析、归一化、附件、存储、导出与管线。运行错误对象只使用这些稳定代码；HTTP 非 2xx 使用 `FETCH_HTTP_ERROR`，具体状态放在错误消息和上下文中。
