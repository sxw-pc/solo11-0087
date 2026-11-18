# 标讯锚（tender-anchor）

**标讯锚**是一个纯本地、配置驱动、可重复运行的标讯采集与核验工作台。它把一条完整的离线数据链路收敛为可测试的工程闭环：

> 本机 mock 标讯门户 → 列表翻页 → 详情抽取 → 金额/日期归一化 → 更正公告关联 → 附件采集 → SQLite 幂等入库 → 质量规则复核 → 只读复核台与结构化导出

在采集链路之上，仓库提供**离线评审裁定**：`fixtures/panel/case.json` 与
`src/tender_anchor/panel_intake.py` 把离线评审票、备选评委和利益冲突披露关联到已采集招标及按时投标，计算首评与合法复评的赢家，并持久化评审版本、授标与更正证据。详见 [评审裁定](#离线评审裁定)。

项目内置虚构标讯门户和样本附件，运行期只允许访问 `127.0.0.1`、`localhost`、`::1`。所有单位、项目、编号、公告、附件内容均为合成数据，不对应任何真实采购活动。

## 快速开始

环境要求：Python 3.11+，本机已可离线使用 Flask、requests、BeautifulSoup4、PyYAML、pytest。

```bash
python -m pip install ".[test]"

# 一条命令启动内置 mock 门户、执行完整采集并导出
python -m tender_anchor demo --crawl

# 运行全部测试
pytest -q
```

安装之后可直接从仓库根目录运行。若不想安装包，也可使用：

```bash
PYTHONPATH=src python -m tender_anchor demo --crawl
PYTHONPATH=src pytest -q
```

### 演示数据的预期结果

演示数据固定为 8 条公告、3 个列表页：

| 指标 | 预期值 | 说明 |
|---|---:|---|
| 公告数 | 8 | 招标 4、更正 2、结果 2 |
| 列表页 | 3 | 每页 3 条，最后一页 2 条 |
| 附件成功 | 5 | 内容哈希与本地路径均记录 |
| 附件失败 | 1 | `TA-2026-0005` 的附件故意不存在，错误码 `FETCH_HTTP_ERROR` |
| 更正关系 | 2 | 0003 → 0001，0007 → 0006 |
| 未处理复核项 | 2 | 缺少预算 + 附件失败 |
| 运行状态 | `completed_with_errors` | 0005 的“详见附件”无法归一化为金额，属于故意设置的脏数据 |

再次运行同一演示目录时，公告、版本、原始留档、附件与复核项数量保持不变，`notices_unchanged` 为 8，用于验证幂等性。运行记录会按每次运行追加。

## 常用命令

```bash
# 只启动内置 mock 门户（127.0.0.1:5810）
python -m tender_anchor serve-portal

# 在另一个终端采集已启动的门户，写入 data/
python -m tender_anchor crawl

# 启动只读复核台（127.0.0.1:5811）
python -m tender_anchor serve-review --data-dir data

# 从已有数据库重新生成 data/exports/notices.json 与 notices.csv
python -m tender_anchor export --data-dir data

# 核对离线评审票、披露并裁定授标（--record 持久化，--export 写 panel.json）
python -m tender_anchor panel-intake --data-dir data_demo \
  --case fixtures/panel/case.json --record --export
```

演示数据默认写入 `data_demo/`，手工采集默认写入 `data/`。复核台可以指向任一数据目录，例如：

```bash
python -m tender_anchor serve-review --data-dir data_demo
```

## 数据与产物

```text
<data-dir>/
├── tender_anchor.sqlite3          # 标讯、版本、附件、更正、复核、运行、审计
├── raw/                           # 原始 HTML 留档（列表页与详情页）
├── attachments/                   # 附件内容（哈希前缀命名）
├── exports/
│   ├── notices.json               # 固定字段顺序的机器可读导出
│   ├── notices.csv                # 固定列顺序的表格导出
│   └── panel.json                 # 评审版本、授标与更正证据导出
└── reports/
    └── run-<run_id>.json          # 本次运行台账与稳定错误列表
```

SQLite 中保存运行台账、字段来源、原始响应哈希、版本快照、附件状态、更正关系、复核队列和审计事件。

## 领域能力

- **公告类型**：招标公告、更正公告、结果公告；列表与详情均可识别。
- **列表翻页**：按配置的 `page_size` 与 `max_pages` 抓取，检测分页循环。
- **详情抽取**：标题、公告编号、项目编号、地区、采购单位、代理机构、发布时间、截止时间、预算、成交金额、成交供应商、附件。
- **归一化**：支持 `￥1,280,000.00元`、`135万元`、`1.5亿元`；支持中文日期、斜杠日期、ISO 风格日期；统一输出 `+08:00` ISO 8601。
- **更正关系**：记录更正公告与原公告的关系及变更字段，数值/时间变更同时保留原始值与归一化值。
- **附件采集**：只下载白名单内链接，防目录穿越，按 SHA-256 命名，保留成功/失败原因。
- **幂等入库**：同一公告相同 HTML 不新增版本；内容变化时版本号递增并保留历史。
- **质量复核**：必填字段、预算区间、截止时间早于发布时间、附件失败、结果公告缺少成交信息、更正原公告不存在等规则。
- **离线评审裁定**：预算价仅作准入，合格投标按有效技术票平均分授标，同分比报价；支持首评与两份合法回避复评，见下节。
- **只读复核台**：概览、公告列表/详情、复核队列、评审案件、运行台账、审计事件、JSON/CSV 导出；不提供任何写接口。

## 离线评审裁定

`fixtures/panel/case.json` 描述一个自包含的采购轮次：两封按时投标（均低于预算）、
R1–R4 技术票、R1 对 `supplier-a` 的迟到财务关联披露，以及两份各有明确授权的回避合同。

计分规则在案件中锁定为 `budget_gate_mean_then_price`：

- 预算价只用于准入，不参与打分；公告截止时间或预算变化会立刻改变可计分投标集合与最终授标资格。
- 每标的有效技术票取平均分；`quorum` 为 3，**每标必须有 3 张独立有效票**才产生均分与赢家。
- 均分相同则报价更低者胜；再相同按 `bid_id` 稳定排序。
- 只有 `initial_review_at` 之前已知的披露计入首评；之后的迟到披露仅记为 `late`，绝不倒写进首评。

两份合法复评路线：

| 合同 | 处置 | A | B | 赢家 |
|---|---|---|---|---|
| 首评 | R1/R2/R3 | 84.67 | 76.00 | supplier-a |
| `whole_round_recusal` 整轮回避 | 剔除 R1 的两票，R4 对两标补票，三人同组重评 | 79.33 | 85.67 | **supplier-b** |
| `independent_absolute` 独立绝对评分 | 只剔除 R1-A，R4-A 补票，R1-B 仍有效 | 79.33 | 76.00 | **supplier-a** |

`case.json` 以整轮回避为治理合同；`fixtures/panel/case_independent.json` 以独立绝对评分为治理合同。
两条路线都不允许把两票与三票均分混比。若披露时机、评委可用性或替补票缺失导致 quorum 不足，
评审状态为 `pending_verification` 且不宣布赢家（见 `case_missing_alternate.json`、
`case_unavailable_alternate.json`）。

持久化保证：

- 首评为版本 1 且不可被新披露覆盖。迟到披露只能通过已授权合同产生新版本。
- 重复导入相同票/披露/合同重放原结果；同一 ID 改分、改披露对象或改案件范围（公告/轮次/规则）会抛出稳定冲突码。
- 公告更正改变准入时，新增 `notice_update` 版本而非改写首评，保留可追溯链。
- 评审版本、逐票计入/剔除、最终授标（`panel_awards`）、授标更正（`panel_corrections`）与审计事件均落库，并可导出 `exports/panel.json`。


## 项目结构

```text
config/                 采集配置与复核规则
docs/                   架构、配置、数据字典、合规、验收
src/tender_anchor/
  config.py             YAML 校验、loopback 白名单检查
  fetch.py              统一 HTTP 入口，连接前 URL 策略校验与审计
  extract.py            列表/详情选择器抽取
  normalize.py          金额、时间、哈希、文本归一化
  pipeline.py           单次采集编排
  pipeline_helpers.py   分页、归一化、附件与入库步骤
  store.py              幂等写库、版本、复核状态
  store_queries.py      SQLite 查询与只读查询封装
  rules.py              质量规则
  export.py             确定性 JSON/CSV、评审导出与运行报告
  web.py                只读 Flask 复核台
  bid_intake.py         离线投标信封准入
  panel_base.py         评审身份/时间/金额共享校验原语
  panel_case.py         评审案件结构、身份、范围校验
  panel_contract_terms.py  两份回避合同的授权与范围校验
  panel_votes.py        逐票计分、均分、排序与评审装配
  panel_contract.py     回避合同的复评票务
  panel_rules.py        首评、复评编排与最终授标
  panel_evidence.py     证据幂等落库与冲突检测
  panel_store.py        评审版本、授标、更正与审计
  panel_intake.py       离线评审对外入口
  panel_views.py        评审案件只读页面
  mock_portal/          loopback-only 虚构标讯门户与 fixture
tests/                  配置、抽取、门户、存储、附件、规则、管线、评审、复核台测试
```

更详细内容见：

- [架构说明](docs/ARCHITECTURE.md)
- [配置说明](docs/CONFIG.md)
- [数据字典](docs/DATA_DICTIONARY.md)
- [合规与离线边界](docs/COMPLIANCE.md)
- [验收清单](docs/ACCEPTANCE.md)

## 边界与非目标

本项目**不做**真实招投标业务、投标/竞价/支付/合同/供应商注册，不连接真实平台，不做浏览器自动化、验证码绕过、登录模拟、反爬对抗、通用爬虫框架、分布式调度、附件全文解析、OCR 或 BI 报表。复核台只读，仅用于本地数据核验。

## 测试

```bash
pytest -q
```

测试不访问外部网络，使用随机 loopback 端口的临时 mock 门户与临时数据目录。覆盖：

- 配置校验、外部地址拒绝、非法选择器、路径穿越拒绝
- 金额/日期归一化和稳定哈希
- 门户分页、详情、附件 404
- 列表/详情抽取、更正关系、期望 fixture
- 通知版本、原始留档、附件与复核队列幂等
- 附件存储、越界文件名、外链拒绝
- 质量规则及稳定排序
- 完整管线、二次运行幂等、确定性导出
- 只读复核台页面、过滤、错误码与下载接口
- 离线评审：首评与两份合法复评的不同赢家、缺替补/替补不可用待核、同分比价、公告更正改变准入
- 评审持久化：版本链、授标与更正证据、相同证据重放、改分/改披露冲突、迟到披露不可倒写
