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

### 2.5 跨标投标保证覆盖

在已采集库（含更正公告 TA-2026-0007）上依次执行：

```bash
python -m tender_anchor bond-preview --bond-case fixtures/bonds/shared_security.json
python -m tender_anchor bond-intake --bond-case fixtures/bonds/shared_security.json
python -m tender_anchor bond-reallocate \
  --bond-case fixtures/bonds/shared_security.json \
  --action fixtures/bonds/reallocation_authorized.json
```

验收要求：

- `bond-preview` 不占用额度；G-01 在延期后对 BID-004 的 `usable_for_bids` 不含 BID-004，阻断原因为 `expires_before_effective_deadline`；工具名义总额 60000 不等同可用保证。
- 固定合同受理后 BID-001 `security_covered`（CASH-01 20000 + G-01 5000），BID-004 `security_shortfall`，仅得 G-02 自由额度 10000；既有现金质押不被静默挪用。
- 删除更正 TA-2026-0007 后（延期前）重算，G-01 恢复对 BID-004 可用，有效截止回到 2026-12-08。
- 预授权再分配后两标的均 `security_covered`：BID-001 由 G-01 25000 覆盖，BID-004 由 CASH-01 20000 + G-02 8000 覆盖；台账逐步回放不超面额、已提交标的不失保。
- 相同案件/动作重放返回原结果且不新增台账；同 ID 不同材料分别报 `different evidence` / `different terms`。
- 超面额动作（`reallocation_over_face.json`）与缺授权动作（`reallocation_without_authorization.json`）被拒绝且不留痕；公告或保函证据缺失时为待核状态。
- 生成 `exports/bond_coverage.{json,csv}`，复核台 `/bonds` 与两个导出路由可读，POST 返回 405。
- 原投标信封首次判定与中标轮次归属语义不变；本功能不裁定结果公告归属或信封修订。

## 3. 测试矩阵

| 测试文件 | 覆盖点 |
|---|---|
| `test_bond_coverage.py` | 两合同资格、延期前后有效期、名义总额≠可用、资金不超占、贪心反例、质押替换原子性与逐步不失保、重放/冲突、缺证据、持久化重开、导出 |
| `test_config.py` | 合法配置、配置指纹、外部地址、非法选择器、缺文件、路径越界、安全文件名、规则 |
| `test_normalize.py` | 金额格式、非法金额、中文/斜杠日期、时区、日期边界、稳定哈希、文本清理 |
| `test_mock_portal.py` | 分页、详情、合成声明、无外部资源、附件 404、健康与非法页 |
| `test_extract.py` | 列表条目、期望 fixture、更正关系、结果公告、稳定解析错误 |
| `test_store_idempotency.py` | 公告/版本/快照/附件/更正/复核项幂等与解决状态 |
| `test_attachments.py` | 附件落盘、哈希、404、目录穿越、外部附件拒绝 |
| `test_rules.py` | 必填、预算区间、截止时间、附件失败、结果缺失、更正关联、排序 |
| `test_pipeline.py` | 端到端、二次运行、导出顺序、外链阻断、重定向阻断 |
| `test_web.py` | 页面、过滤、安全头、错误码、只读、下载、缺少导出 |

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

- Python 代码（含测试）总量在 2500–4500 行之间。
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
