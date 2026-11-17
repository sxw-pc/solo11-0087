# 标讯锚（tender-anchor）

隔离业务素材包含 `fixtures/clarifications/case.json` 与
`src/tender_anchor/clarification_intake.py`：它将本地澄清答复、公开状态和参与者
接触事件关联到已采集公告，并由 `src/tender_anchor/parity.py` 与
`parity_evidence.py` 裁定信息对等资格（公开可达 / 逐人收讫两种合同模式），
通过 `clarify` 子命令落库、审计并导出可回读的证据链。

**标讯锚**是一个纯本地、配置驱动、可重复运行的标讯采集与核验工作台。它把一条完整的离线数据链路收敛为可测试的工程闭环：

> 本机 mock 标讯门户 → 列表翻页 → 详情抽取 → 金额/日期归一化 → 更正公告关联 → 附件采集 → SQLite 幂等入库 → 质量规则复核 → 只读复核台与结构化导出

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
│   └── notices.csv                # 固定列顺序的表格导出
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
- **澄清信息对等裁定**：把澄清问题、私答草稿、匿名化正式条款、参与者接触事件与公告更正关联成证据链，按合同模式裁定共同条款资格；不从页面现状倒填任何人过去的知情权。
- **只读复核台**：概览、公告列表/详情、复核队列、运行台账、审计事件、JSON/CSV 下载；不提供任何写接口。

### 澄清信息对等裁定

```bash
# 对一份离线澄清证据案件做裁定并落库（需先 crawl/demo 采集公告与更正）
python -m tender_anchor clarify --data-dir data_demo \
  --case-file fixtures/clarifications/case.json
```

裁定只依据显式证据，关键规则：

- **证据链硬检查**：招标公告已采集；同一 `answer_id` 不得对应不同正文；正式公开条款唯一且已匿名化；私答草稿不得被非收件人接触；至少存在一条对公开版的接触；截止时间证据（原公告或更正）存在。
- **7 日准备期**：自公开版发布时刻起，到适用截止时刻须满足完整 7 日。优先采用已核实更正公告中的截止时间（如 TA-2026-0003 把 TA-2026-0001 的 12-01 延至 12-05）；无更正时退回原公告截止，窗口不足则拒绝。
- **公开可达合同（public_open）**：正式条款已匿名化、真实公开且准备期足够即可认证，不要求每位投标人都点击。
- **逐人收讫合同（per_person_receipt）**：每位报名者都须有对同一正式版本的独立收讫事件；私答接触不计入公开收讫，迟到下载保留其真实时间、不倒填为发布日；缺收讫时整体待核。
- **终局不可变**：已发出的 `certified`/`rejected` 认证不被重放重写；证据或更正截止变化引发冲突时直接报错。`pending`/`unverifiable` 为临时结论，允许补证后演进。
- 产物写入 `exports/clarification_parity.json`，并在 `audit_events` 留下每次导入与每个合同模式的审计记录。

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
  clarification_intake.py  澄清案件读取、证据校验与指纹
  clarification_store.py   澄清证据/对等认证的幂等落库与冲突保护
  parity_evidence.py    证据链组装、截止证据与 7 日准备期
  parity.py             两种合同模式的信息对等裁定
  store.py              幂等写库、版本、复核状态
  store_queries.py      SQLite 查询与只读查询封装
  rules.py              质量规则
  export.py             确定性 JSON/CSV 与运行报告
  web.py                只读 Flask 复核台
  mock_portal/          loopback-only 虚构标讯门户与 fixture
tests/                  配置、抽取、门户、存储、附件、规则、管线、复核台测试
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
