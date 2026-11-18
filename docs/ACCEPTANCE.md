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

### 2.4 只读复核台

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

## 2.5 离线评审裁定

```bash
python -m tender_anchor panel-intake --data-dir data_demo \
  --case fixtures/panel/case.json --record --export
```

验收要求：

- 首评 `winner_bidder_id == "supplier-a"`，A 84.67 / B 76.00，迟到披露 `DISC-1` 被记为 `late` 而非首评已知。
- 整轮回避复评剔除 R1 对两标的票、以 R4 补票，A 79.33 / B 85.67，赢家 `supplier-b`。
- 独立绝对评分复评只剔除 R1-A，A 79.33 / B 76.00，赢家 `supplier-a`。
- 每个可计分投标恰好 3 张独立有效票；两票与三票均分不混比。
- `fixtures/panel/case_missing_alternate.json` 与 `case_unavailable_alternate.json` 返回 `pending_verification`，不宣布赢家。
- 重复执行同一命令重放原版本，不新增评审版本；改分或改披露对象返回 `PANEL_EVIDENCE_CONFLICT`。
- 复核台 `/panels`、`/panels/<case_id>` 与 `/export/panel.json` 可读。

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
| `test_panel_scoring.py` | 首评、整轮/独立回避复评的不同赢家、缺替补、不可用替补、同分比价、公告更正改变准入 |
| `test_panel_persistence.py` | 版本链、授标、更正证据、重放、改分/改披露冲突、迟到披露不可倒写、公告更正版本 |
| `test_panel_validation.py` | 身份、范围、合同授权、整轮/绝对范围、票分数、披露与投票关联 |
| `test_panel_cli.py` | 命令行预览/落库/导出、面板页面、冲突错误码 |

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

- Python 代码（含测试）总量在 2500–7500 行之间（评审裁定模块计入后约 6700 行）。
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
