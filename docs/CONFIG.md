# 配置说明

配置文件分为两类：

- `config/sources.yaml`：门户地址、loopback 白名单、分页、超时、响应大小上限、CSS 选择器。
- `config/rules.yaml`：必填字段、预算上下限。

配置在采集启动前一次性读取和校验。配置错误不会被静默忽略，CLI 会输出稳定的 JSON 错误对象并以退出码 2 结束。

## 1. sources.yaml

```yaml
portal:
  base_url: "http://127.0.0.1:5810"
  list_path: "/notices"
  allowed_hosts:
    - "127.0.0.1"
    - "localhost"
    - "::1"
  timeout_seconds: 5.0
  page_size: 3
  max_pages: 10
  user_agent: "tender-anchor-local/1.0"

limits:
  max_response_bytes: 1048576
  max_attachment_bytes: 524288

selectors:
  list_item: "ul.notice-list li.notice-item"
  list_link: "a.notice-link"
  list_title: "span.notice-title"
  list_date: "span.notice-date"
  list_kind: "span.notice-kind"
  next_page: "nav.pagination a.next-page"
  detail_title: "h1.notice-title"
  meta_row: "table.notice-meta tr"
  body: "div.notice-body"
  attachments: "section.attachments a.attachment-link"
  correction_diff: "table.correction-diff tr"
```

### portal

| 字段 | 必填 | 默认 | 约束 | 语义 |
|---|---|---|---|---|
| `base_url` | 是 | — | `http`/`https`，host 必须是 loopback 且出现在白名单 | 门户根地址 |
| `list_path` | 否 | `/notices` | 以单个 `/` 开头的站内路径 | 列表入口 |
| `allowed_hosts` | 否 | `127.0.0.1, localhost, ::1` | 每项必须是 loopback host | 连接前白名单 |
| `timeout_seconds` | 否 | `5.0` | `0.1..30.0` | 单次请求超时 |
| `page_size` | 否 | `3` | `1..100` | 列表查询参数 |
| `max_pages` | 否 | `10` | `1..100` | 防止无限翻页 |
| `user_agent` | 否 | `tender-anchor-local/1.0` | 非空字符串 | 本地客户端标识；不用于伪装浏览器 |
| `max_response_bytes` | 否 | `1048576` | `1024..10485760` | HTML/列表响应上限 |
| `max_attachment_bytes` | 否 | `524288` | `1024..10485760` | 单个附件上限 |

> 只有 loopback host 可通过校验。即使把公网地址写进 `allowed_hosts`，配置也会被拒绝。`base_url` 的 host 必须出现在 `allowed_hosts` 中。

### selectors

所有键必填，值为 CSS 选择器。加载时用 BeautifulSoup 实际编译一次，非法选择器会得到 `CONFIG_SELECTOR_INVALID`。

| 键 | 抽取目标 |
|---|---|
| `list_item` | 每个列表条目容器 |
| `list_link` | 条目内的详情链接 |
| `list_title` | 条目标题 |
| `list_date` | 条目发布日期文本 |
| `list_kind` | 条目公告类型文本 |
| `next_page` | 下一页链接；不存在表示最后一页 |
| `detail_title` | 详情页主标题 |
| `meta_row` | 元数据表的行；每行需有 `th` 与 `td` |
| `body` | 公告正文 |
| `attachments` | 附件链接 |
| `correction_diff` | 更正事项行；行应带 `data-field` 属性 |

支持的元数据标签：`公告编号`、`公告类型`、`项目编号`、`地区`、`采购单位`、`代理机构`、`发布时间`、`截止时间`、`预算金额`、`成交金额`、`成交供应商`、`关联原公告`。缺失标签不会导致整页失败；规范字段为空后由复核规则报告。

公告类型映射：

- `招标公告`、`采购公告` → `tender`
- `更正公告`、`变更公告` → `correction`
- `结果公告`、`中标公告`、`成交公告` → `result`
- 其他 → `other`

## 2. rules.yaml

```yaml
required_fields:
  - "title"
  - "project_code"
  - "region"
  - "purchaser"
  - "agency"
  - "publish_at"
  - "deadline_at"
  - "budget_amount"

min_budget: 1000
max_budget: 500000000
```

| 字段 | 语义 |
|---|---|
| `required_fields` | 对招标/更正公告强制检查的规范字段名；结果公告自动豁免 `deadline_at` |
| `min_budget` | 预算下限（Decimal，允许 0） |
| `max_budget` | 预算上限，必须大于下限 |

`required_fields` 可使用的规范字段：`notice_id`、`title`、`project_code`、`region`、`purchaser`、`agency`、`publish_at`、`deadline_at`、`budget_amount`、`award_amount`、`winner`、`related_notice_id`。

### 内置规则代码

| 规则代码 | 级别 | 触发条件 |
|---|---|---|
| `MISSING_<FIELD>` | warning | 必填规范字段为空 |
| `BUDGET_OUT_OF_RANGE` | warning | 预算不在 `[min_budget, max_budget]` |
| `DEADLINE_BEFORE_PUBLISH` | error | 截止时间早于发布时间 |
| `ATTACHMENT_FETCH_FAILED` | error | 至少一个附件未成功存储 |
| `RESULT_AWARD_MISSING` | warning | 结果公告缺少成交金额或成交供应商 |
| `CORRECTION_ORIGINAL_MISSING` | error | 更正公告关联的原公告不在本次公告集 |
| `CORRECTION_CHANGES_EMPTY` | warning | 更正公告没有提取到任何变更项 |

## 3. 金额与时间规范

金额：

- 支持带 `￥`、`¥`、`人民币`、`元` 前缀/后缀，逗号分隔，`万`/`万元`/`亿`/`亿元` 单位。
- 输出为两位小数的十进制字符串，如 `1280000.00`；货币固定为 `CNY`。
- 无数字、零或负数会记录 `NORMALIZE_AMOUNT`。

时间：

- 支持 `2026年12月01日 09:30`、`2026-12-03 14:00`、`2026/12/08 10:00`。
- 仅发布日期时使用当天 `00:00:00`；截止时间缺时间时使用 `23:59:59`。
- 所有输出为 `Asia/Shanghai` 时区 ISO 8601，例如 `2026-12-01T09:30:00+08:00`。
- 非法日期记录 `NORMALIZE_DATE`，不会猜测。

## 4. 端口与手工运行

默认 mock 门户为 `127.0.0.1:5810`，复核台为 `127.0.0.1:5811`。可调整端口，但配置中 `base_url` 必须与实际门户端口一致。

```bash
python -m tender_anchor serve-portal --port 5810
python -m tender_anchor crawl --config config/sources.yaml --data-dir data
python -m tender_anchor serve-review --data-dir data --port 5811
```

`serve-portal` 与 `serve-review` 都固定绑定 `127.0.0.1`，不提供“绑定 0.0.0.0”的选项。

## 5. 配置错误码

| 错误码 | 含义 |
|---|---|
| `CONFIG_NOT_FOUND` | 文件不存在或不可读 |
| `CONFIG_INVALID` | 缺少映射、值类型错误、路径格式错误 |
| `CONFIG_HOST_FORBIDDEN` | 非 loopback 地址或 host 不在白名单 |
| `CONFIG_SELECTOR_INVALID` | CSS 选择器语法错误 |
| `CONFIG_VALUE_OUT_OF_RANGE` | 分页、超时或大小超出范围 |
| `RULE_CONFIG_INVALID` | 规则字段或预算区间非法 |

## 6. 离线修改建议

- 只调整虚构门户的路由、分页和选择器，不要把 `base_url` 改成任何非 loopback 地址。
- 修改规则后先运行 `pytest -q`，再运行 `python -m tender_anchor demo --crawl`。
- 更换演示数据时，建议使用新的 `--data-dir` 或先备份 SQLite；系统保证幂等，但不会删除旧公告。
