# 验收清单

## 1. 环境与安装

```bash
python -m pip install ".[test]"
```

要求 Python 3.11+。若完全离线，需提前准备 Flask、requests、BeautifulSoup4、soupsieve、PyYAML、pytest 的本地 wheel。

## 2. 必跑命令

### 2.1 演示

```bash
python -m tender_anchor demo --crawl
```

验收要求：

- 退出码为 0。
- 命令自行启动 `127.0.0.1:5810` mock 门户并在结束后关闭。
- 输出 JSON 中包含 `demo: completed`、数据目录、运行摘要和复核台启动提示。
- `summary.pages_fetched == 3`。
- `summary.notices_seen == 8`、`notices_inserted == 8`（首次运行）。
- `summary.attachments_ok == 5`、`attachments_failed == 1`。
- `summary.review_open == 2`。
- `summary.status == "completed_with_errors"`，原因是 fixture 故意包含一条无法归一化的预算文本。
- `summary.errors` 中存在 `NORMALIZE_AMOUNT`，target 指向 `TA-2026-0005#budget_amount`。
- 生成 `data_demo/tender_anchor.sqlite3`、`raw/`、`attachments/`、`exports/notices.{json,csv}` 与 `reports/run-*.json`。

`completed_with_errors` 不代表流程崩溃：8 条公告均已入库，1 个失败附件和 1 个缺失预算已进入复核队列。

### 2.2 幂等复跑

再次执行同一条命令：

- `summary.notices_inserted == 0`。
- `summary.notices_updated == 0`。
- `summary.notices_unchanged == 8`。
- 公告、版本、原始快照、附件、复核项数量不增加。
- `runs` 数量随执行次数增加。
- JSON/CSV 中公告顺序仍按 `notice_id` 稳定排序。

### 2.3 测试

```bash
pytest -q
```

验收要求：全部测试通过，无失败。测试使用临时目录和随机 loopback 端口，不访问外部网络。

### 2.4 澄清信息对等裁定

先完成一次采集（demo 或 crawl），再执行：

```bash
python -m tender_anchor clarify --data-dir data_demo \
  --case-file fixtures/clarifications/case.json
```

验收要求：

- 退出码为 0，输出包含两个合同模式的裁定与导出路径。
- 基线案件中 `public_open` 为 `certified`（依据 TA-2026-0003 更正后的 12-05 截止，准备期 10 天）；
  `per_person_receipt` 为 `pending`（supplier-a 仅有私答接触，supplier-b 26 日才下载公开版）。
- 再次执行同一案件：`outcome` 为 `unchanged`，认证行与 `decision_hash` 不变。
- 导入 `fixtures/clarifications/cases/public_receipt_complete.json`：逐人收讫转为 `certified`。
- 对已认证案件改动任一接触事件后重导：退出码 2，错误码 `CLARIFICATION_CONFLICT`，原认证保留。
- 生成 `exports/clarification_parity.json`，内容与库内回读一致；审计事件含每次导入与两个合同模式。

### 2.5 只读复核台

```bash
python -m tender_anchor serve-review --data-dir data_demo
```

打开 `http://127.0.0.1:5811`，验收页面：

- `/`：统计卡片、最近标讯、未处理复核项、导出入口。
- `/notices`：类型和关键词过滤。
- `/notices/TA-2026-0001`：元数据、附件、更正关系、字段来源、质量项、原始留档。
- `/review`：未处理/已解决复核项。
- `/runs`：运行台账和错误 JSON。
- `/audit`：审计事件。
- `/export/notices.json`、`/export/notices.csv`：下载文件。
- POST 到任意页面返回 405；不提供写入能力。
- 页面无外部脚本、样式、字体或图片引用。

## 3. 测试矩阵

| 测试文件 | 覆盖点 |
|---|---|
| `test_config.py` | 合法配置、配置指纹、外部地址、非法选择器、缺文件、路径越界、安全文件名、规则 |
| `test_normalize.py` | 金额格式、非法金额、中文/斜杠日期、时区、日期边界、稳定哈希、文本清理 |
| `test_mock_portal.py` | 分页、详情、合成声明、无外部资源、附件 404、健康与非法页 |
| `test_extract.py` | 列表条目、期望 fixture、更正关系、结果公告、稳定解析错误 |
| `test_store_idempotency.py` | 公告/版本/快照/附件/更正/复核项幂等与解决状态 |
| `test_attachments.py` | 附件落盘、哈希、404、目录穿越、外部附件拒绝 |
| `test_rules.py` | 必填、预算区间、截止时间、附件失败、结果缺失、更正关联、排序 |
| `test_pipeline.py` | 端到端、二次运行、导出顺序、外链阻断、重定向阻断 |
| `test_web.py` | 页面、过滤、安全头、错误码、只读、下载、缺少导出 |
| `test_clarification_intake.py` | 澄清案件校验、匿名化、私答泄露、参与者/事件身份、指纹 |
| `test_parity_adjudication.py` | 两合同资格、原/更正截止、7 日窗口、私答隔离、迟到签收、冲突正文 |
| `test_clarification_persistence.py` | 证据链落库、幂等重放、审计、导出回读、CLI 离线入口 |
| `test_clarification_conflicts.py` | 认证后证据/更正变更冲突、待核补证演进、截止依据回退 |

## 4. 安全验收

- 配置中的非 loopback `base_url` 被拒绝，错误码 `CONFIG_HOST_FORBIDDEN`。
- 非白名单附件 URL 被拒绝，错误码 `FETCH_HOST_BLOCKED`，且测试确认 HTTP 客户端未被调用。
- 3xx 响应被拒绝，错误码 `FETCH_REDIRECT_BLOCKED`。
- 附件路径穿越不能写出一层数据目录；本地路径经 `ensure_within`。
- SQLite 复核台只读。
- 页面响应含 CSP、`nosniff`、`DENY` 和 `no-store`。
- 仓库仅含 loopback URL、文档保留网段测试地址和虚构数据。
- 无账号、密码、令牌、Cookie、私钥或证书。

## 5. 工程质量验收

- Python 代码（含测试）总量在 2500–6000 行之间。
- 任何 Python 文件不超过 300 行。
- 运行依赖均为常见本机包，无浏览器二进制、GPU、外部数据库。
- 错误使用稳定错误码；运行错误可解释。
- 数据库记录、复核项、报告字段和导出顺序稳定。
- 所有运行期输出位于数据目录内。
- README、架构、配置、数据字典、合规和验收文档齐全。

## 6. 已知预期异常

演示 fixture 的两个设计内异常：

1. `TA-2026-0005` 的预算字段为“详见附件”，无法归一化为金额，产生运行错误 `NORMALIZE_AMOUNT` 和复核项 `MISSING_BUDGET_AMOUNT`。
2. `TA-2026-0005` 链接的 `missing_attachment.txt` 在 mock 门户中不存在，附件状态为 `failed`、错误码 `FETCH_HTTP_ERROR`，并产生复核项 `ATTACHMENT_FETCH_FAILED`。

这两项用于验证稳定错误码、失败隔离、附件错误处理和复核台展示，不应被“修复”为静默猜测值。

## 7. 完成判定

以下全部满足即验收通过：

- 两个必跑命令成功（演示退出码 0，测试全绿）。
- 首次演示与二次演示指标符合上文。
- 复核台指定页面与下载可用。
- 安全验收全部通过。
- 文档与实现字段、规则、计数保持一致。
