# 语料补全计划与实施记录（v2 → v3）

> 状态：**v2、v3 均已合入本地语料并完成评测**（2026-10-02）。语料文件、官方原件与片段均不入仓库（.gitignore），本文档记录计划、来源留痕、缺口账与实测数字；采集与转换代码在仓库。

## 0. 缺口是怎么定的

v1 语料（14,212 条 / 238 部）源自 legal-wisdom 的公报 PDF 解析库，三类缺口：

1. **覆盖缺口**：民法典、刑法、行政处罚法、劳动合同法、劳动法、刑事诉讼法等最常被问到的法律正文缺失（源头库就没有）；
2. **质量缺口**：公报双栏 PDF 交错解析造成乱序污染（见 README「已知失败案例」）；
3. **口径缺口**：修订版本并存、行号标签偏移。

v2 补了缺口 1 里最要紧的六部大法，但「还漏多少」当时没有基准。v3 换了做法：**拿一份法考电子法条汇编当对账基准**，逐部核对，把缺口算成具体部数与条数，再逐部补。

## 1. 对账基准与缺口账

基准用竹马（zhumavip.com）法考「法律法规汇编」（`businessTypeId=104`）：法考八科目录，共 **243 个法律节点、去重后 242 部**——它不是全部法律，但是一个口径清晰、科目完整的考试用汇编，适合当「该有的都在不在」的清单。

| 项 | 部数 |
|---|---|
| 竹马目录（去重） | 242 |
| 其中语料已有 | 31 |
| **待补缺口** | **211** |
| ├ 竹马 web 端有正文 → 直接采 | 65 |
| └ 竹马 web 端无正文 → flk 官方源采 | 146（命中 123） |
| 补入后仍未覆盖 | 23 |

逐部名单、每部的条数来源与判定理由在 `data/flk/tmp/*_report.txt`（本地产物）。

## 2. v2：flk 官方 docx，六部大法 × 2,311 条

**来源**：国家法律法规数据库 flk.npc.gov.cn（全国人大官方库）。选官方 docx 而非 PDF：原生文本零解析残片、单栏无交错，从源头规避 v1 的两类解析污染。

| 法律 | 版本（公布/施行） | 条数 | id 段 |
|---|---|---|---|
| 中华人民共和国民法典 | 2020-05-28 / 2021-01-01 | 1260 | 910001-911260 |
| 中华人民共和国刑法 | 2020-12-26 / 2021-03-01 | 452 | 920001-920452 |
| 中华人民共和国行政处罚法 | 2021-01-22 / 2021-07-15 | 86 | 930001-930086 |
| 中华人民共和国劳动合同法 | 2012-12-28 / 2013-07-01 | 98 | 940001-940098 |
| 中华人民共和国劳动法 | 2018-12-29 / 2018-12-29 | 107 | 950001-950107 |
| 中华人民共和国刑事诉讼法 | 2018-10-26 / 2018-10-26 | 308 | 960001-960308 |

逐部条数与官方一致；零 (cid:) 残片；条号经单调递增校验。

## 3. v3：双源补齐 188 部 × 8,750 条

### 3.1 来源一：竹马电子法条阅读器（65 部 / 6,664 条）

接口（登录态，页面内调用；网页阅读器走 CDN 网关 `online-web-api-cdn`，与主站 `/java-api` 同一后端、同一 `traceId`）：

```
POST /java-api/api/laws/list  {businessTypeId:104} → 学科→法律→章节 目录树
POST /java-api/api/laws/info  {id:<法律id>}        → 该法全部章节全文（含正文的各章）
```

**一个重要发现（可自行复核）**：这棵树里 243 个法律节点中，**只有 89 个挂了章节正文节点**，其余 154 个只有名字、`info` 接口返回 `data:[]`（不是权限错，是后端没存）；网页上点这些法律，右栏为空、不发任何请求。复核方法：在该页点「民法典」「刑法」有全文，点「刑法修正案（十一）」「反分裂国家法」为空。竹马 **APP** 端可能是全量，但 APP 接口需 APP 鉴权，网页拿不到。

### 3.2 来源二：flk 官方文件（123 部 / 2,086 条）

对上面「无正文」的 146 部，逐部到 flk 按标题检索、下载官方文件：

```
页面内 POST /law-search/search/list              → 取 bbbs；searchType=1（精确）、sxx=3（有效）
                                                    同名多版本时取公布日期最新者（修订整合文本）
页面内 POST /law-search/download/batch [{bbbs,format:"docx"}] → OSS 预签名直链
分片取回字节 → 本地桥落盘 → 质检 → 条级切分 → 片段 JSONL
```

命中 123/146。**未命中 23 部，flk 确实不收录**（逐部试过精确、模糊、去发文机关前缀三种检索）：

- 行业职业道德准则 3 部：检察官/律师/公证员职业道德基本准则（行业规范，非法律法规）；
- 香港基本法附件一、附件二（附件，flk 只收录正文与相关批准决定）；
- 内部控制/工作文件 18 部：如「两高一部关于依法适用正当防卫制度的指导意见」「办理刑事案件庭前会议规程」「规范量刑程序若干问题的意见」「为死刑复核案件被告人提供法律援助的规定」「海上刑事案件管辖等有关问题的通知」等。

### 3.3 切条的四种文体（`articles_from_paragraphs`）

官方与法考汇编文件不止「第X条」一种编号，按体裁分四级，**先试主通道，失败才逐级放宽**，且每条都留 `meta.num_origin` 记原始标记：

| 通道 | 适用文书 | 条号来源 | 本次用法 |
|---|---|---|---|
| `第X条`（主） | 法律、多数司法解释 | 原文「第X条」 | 157 部 |
| `一、` 式 | 刑法修正案、人大常委会决定/法律解释 | 「一、二、」→「第X条」 | 14 部 |
| `N.` 条目号 | 指导意见、试行规定（条目即引用单位） | 「1.」→「第N条」 | 6 部 |
| 整篇一条 | 短法律解释（通篇无编号） | `num="全文"`、`meta.unit="whole-document"` | 11 部 |

后两级是**映射**，不是原文自带条号，故 `num_origin` 与 `unit` 必须随片段保留；下游若需严格口径，可按这两个字段过滤。

### 3.4 合入纪律（append-only）与质检门

- v1/v2 既有行**一个字节不动、id 不变**（真实金标 gold_ids 绑定这些 id）；新片段走保留 id 段：**970000+（竹马）、980000+（flk）**；
- 质检门（`scripts/merge_corpus_v3.py`，任一不过即整体失败、不产半成品）：id 与既有语料冲突、合入集合内 (law, num) 重复、text 空、含 PDF 残片标记 `(cid:`、合入后条数 ≠ 既有+新增。

### 3.5 成果

| 项 | v1 | v2 | v3 |
|---|---|---|---|
| 条数 | 14,212 | 16,523 | **25,273** |
| 法律部数 | 238 | 244 | **432** |
| 竹马目录覆盖率 | — | 31/242 = 12.8% | **219/242 = 90.5%** |

## 4. 实测数字（补全当轮口径：检索重标定前）

> **本节是历史口径存档**：下表是 v3 法条补全当轮、**检索重标定前**的实测数字。检索线合并时按诊断结论重标定了 BM25 长度归一化（b 0.75→0.6）与扩展通道权重（2.5），当前口径见 §4.1——**同一口径的数字请以 §4.1 与 [eval_report.md](eval_report.md) 为准**，不要用本节旧数。

| 口径 | v1（14,212） | v2（16,523） | v3（25,273） |
|---|---|---|---|
| 真实金标 38 题 hybrid R@5 / MRR | 44.7% / 0.312 | 39.5% / 0.246 | **31.6% / 0.232** |
| 真实金标深度 R@10 / R@30 | 55.3% / 78.9% | — / 76.3% | **52.6% / 73.7%** |
| 旧合成金标 177 题 hybrid R@5 | 98.9% | 98.3% | **97.2% / 0.965** |
| 同口径 like 基线 R@5 | 0.0% | 0.0% | 0.0% |

**当轮如实说明（历史）**：语料从 14,212 条扩到 25,273 条后，真实金标 hybrid R@5 当轮从 44.7% 降到 31.6%、深度 R@30 从 78.9% 降到 73.7%。当轮判断原因是**语料变大后词法检索未重标定被稀释**。**该现象已在检索重标定后消失（见 §4.1）**：v3 的 R@5/R@30 回到 44.7% / 81.6%，与 v1 持平。本节数字保留为历史留痕，不再作为当前结论。

当轮 v3 排名分布（真实 38 题，max_k=30）：第 1 名 7 题、2-5 名 5 题、6-10 名 8 题、11-30 名 8 题、未进 30 名 10 题。

### 4.1 重标定后的当前数字（单一来源：[eval_report.md](eval_report.md)）

| 口径（同一份代码） | v1（14,212） | v2（16,523） | **v3（25,273，当前）** |
|---|---|---|---|
| 真实 38 题 hybrid R@5 / MRR | 44.7% / 0.266 | 39.5% / 0.227 | **44.7% / 0.250** |
| 真实 38 题深度 R@10 / R@30 | 55.3% / 81.6% | 52.6% / 81.6% | **52.6% / 81.6%** |
| 合成 177 题 hybrid R@5 | 98.9% | 98.3% | **97.2%** |

**结论修正**：重标定后 v3 真实 R@5 从 31.6% **回到 44.7%**、R@30 从 73.7% **回到 81.6%**——v3 已回到/追平 v1 口径。「语料变大后被稀释」是**当轮尚未重标定**的暂时现象，不是覆盖扩容的固有代价。机制、逐题迁移与全网格见 [retrieval-v3-diagnosis.md](retrieval-v3-diagnosis.md)。

v3 当前排名分布（真实 38 题，max_k=30）：第 1 名 6 题、2-5 名 11 题、6-10 名 3 题、11-30 名 11 题、未进 30 名 7 题。**6-30 名的 14 题是重排通道的工作面**（未进 30 名的 7 题属词法/语料层失败，重排救不了）。

## 5. 已知不足与后续计划（按优先级）

1. **真实金标扩容（38 → 200）**：**部分完成（2026-10-02）**——新增 batch1 **37 题**（真实问句，来源百度知道/找法网，逐条 URL），证据逐字校验 37/37 通过，真实金标合计 **75 题**；另有 35 条真实候选写入 `gold/candidates_unverified.jsonl`（`verified:false`，明示未核验原因；含因法域不匹配剔除的 2 题酒驾/醉驾）待下一轮核验。本轮批次见 [gold/gold_real_v3_batch1.jsonl](../gold/gold_real_v3_batch1.jsonl) 与复核表 [gold-review-worksheet-v3-batch1.md](gold-review-worksheet-v3-batch1.md)。仍未到 200 题：本轮受「问句可核验 + 语料有条文可答」双重约束，能扩到 75 题；下一轮从候选池与更多来源继续。**这是当前最该做的一步**。
2. **映射条号的口径确认**：31 部走回退通道的文书，「一、」/「N.」→「第X条」是依引用惯例做的映射，需法律专业确认是否可作为正式条号引用（`meta.num_origin` 已留痕）。
3. **竹马 APP 端正文**：154 部 web 无正文里，23 部连 flk 都没有（行业准则、内部工作文件）；如这批内容确需入库，需另找来源（如各自发布机关官网）。
4. **公报源污染行替换**（原计划 2）：治安管理处罚法、民诉法、公司法等公报双栏污染法，逐部用 flk 官方版本追加干净行。
5. **逐行解析质量分**（backlog candidate-011）：给 v1 公报源行打质量旗标，输出按法聚合的质检报告。
6. **多版本口径**：同一法律多版本并存的检索竞争，需要在 Citation 输出带版本元数据（`meta.effective`），由上层按时间有效性过滤。
7. **数字进 README/网站**：检索线与合并后已把 README/网站切到当前 v3 口径（单一来源 `docs/metrics.json`，`check_doc_numbers.py` 校验）；本文档 §4.1 与之对齐，历史口径（§4）保留留痕。

## 6. 复现

仓库只带代码，语料与原件不入库；下列步骤需维护者本地执行，其中「获取」一步必须在浏览器会话内完成（两个站点都有 WAF，纯脚本被拦）：

```bash
# ── 1) 获取原件（浏览器会话辅助；落盘目录 data/flk/，不入仓库）
#    a. 竹马：登录后页面内调 /api/laws/list + /api/laws/info，分片取回 → zhuma_laws_raw.json
#       （本地桥脚本 scripts/flk_bridge.py 用于把页面字节直接写盘，绕开上下文）
#    b. flk：对无正文的 146 部精确检索 + download/batch → data/flk/docs_v3/<bbbs>.docx|.doc

# ── 2) 转条级片段（纯标准库；.doc 走 antiword -w 0）
python scripts/zhuma_to_articles.py --raw data/flk/tmp/zhuma_laws_raw.json \
    --catalog data/flk/tmp/zhuma_catalog.json --id-base 970000 \
    --out data/flk/fragments/zhuma_v3.jsonl --report data/flk/tmp/zhuma_report.txt
python scripts/flk_docs_to_articles.py --docs data/flk/docs_v3 \
    --manifest data/flk/tmp/flk_manifest_a.json --id-base 980000 \
    --out data/flk/fragments/flk_v3.jsonl --report data/flk/tmp/flk_docs_report.txt

# ── 3) 合入（append-only + 质检门）
python scripts/merge_corpus_v3.py --base data/corpus_v2.jsonl \
    --fragments data/flk/fragments/zhuma_v3.jsonl data/flk/fragments/flk_v3.jsonl \
    --out data/corpus_v3.jsonl --report data/flk/tmp/merge_v3_report.txt

# ── 4) 评测（本文件第 4 节数字来源）
python scripts/gen_eval_report.py --corpus data/corpus_v3.jsonl

# ── 5) 覆盖率复算（无需 data/ 也能跑：清单入库，语料本地只读）
#    对账基准 corpus/zhuma_catalog_titles.json 由 --emit-titles 从本地竹马目录树提取后入库
python scripts/coverage_report.py --corpus data/corpus_v3.jsonl \
    --catalog corpus/zhuma_catalog_titles.json --md docs/coverage-report.md
#    实测：语料 25273 条 / 432 部；竹马目录去重 242 部，已覆盖 219 部（90.5%），未覆盖 23 部；名同法不同源 1 部

# ── 6) 逐行质检（不改语料，输出独立报告 + json）
python scripts/corpus_audit.py --corpus data/corpus_v3.jsonl \
    --md docs/corpus-audit.md --json data/flk/tmp/corpus_audit.json

# ── 7) 真实金标批次（候选池 → 核验 → 金标；内置 38 题输出逐字节不变）
python scripts/build_real_gold.py --corpus data/corpus_v3.jsonl \
    --spec gold/gold_real_v3_batch1_spec.jsonl --qid-prefix v3 \
    --out gold/gold_real_v3_batch1.jsonl --dropped /tmp/batch1_dropped.jsonl
#    候选池（未核验，只留痕不进金标）：
python scripts/build_real_gold.py --corpus data/corpus_v3.jsonl \
    --spec gold/candidates_unverified.jsonl --out /tmp/cand_gold.jsonl
#    复核工作表（可多批次合并）：
python scripts/make_gold_review_worksheet.py --gold gold/gold_real_v3_batch1.jsonl \
    --status gold/real_v3_batch1_review_status.json --out docs/gold-review-worksheet-v3-batch1.md
```

（v2 的六部大法走 `scripts/docx_to_articles.py`，用法见该脚本 docstring。）

## 7. v3 后的治理产物（2026-10-02）

把「覆盖与质量」从文档里的一句话变成可复算、可审计、可交专业复核的东西：

| 产物 | 文件 | 一句话 |
|---|---|---|
| 覆盖率可复算 | `scripts/coverage_report.py` + [coverage-report.md](coverage-report.md) | 219/242 = 90.5%，覆盖率此前只在本文档、竹马目录不入库；现在一条命令可复算 |
| 逐行质检旗标 | `scripts/corpus_audit.py` + [corpus-audit.md](corpus-audit.md) | 七类旗标：疑似交错**两带** 5010（有排版标记）+ 2193（无标记，低置信，合计 7203 = v1 公报源窄栏行数，**5010 不是上限**）、映射条号 506、重复条号 802、整篇一条 11、超长 4、空/超短 0；后两类整理成复核表 |
| 缺口可追踪 | [../corpus/missing_laws.json](../corpus/missing_laws.json) | 仍未覆盖 23 部的法名/科目/判定依据/建议来源 |
| 版本口径清点 | [../corpus/same_name_laws.json](../corpus/same_name_laws.json) | 跨来源同名 1 部、flk 非现行有效 4 条、v1 内部重复 401 组 |
| 真实金标扩容 | [../gold/gold_real_v3_batch1.jsonl](../gold/gold_real_v3_batch1.jsonl) | 38 → 75 题（batch1 37 题，证据逐字校验 37/37） |
| 进化提案 | [corpus-governance.md](corpus-governance.md) | 覆盖 → 质量 → 版本口径 → 金标代表性 的后续路线 |

**重要提醒（诚实）**：`coverage_report` 的覆盖率、`corpus_audit` 的疑似交错旗标都是**可复算的推理**，不等于「已确证」：
- 覆盖率的分母是竹马法考汇编目录（242 部），不是「全部法律」；未覆盖 23 部多为行业准则/内部工作文件，flk 本就不收。
- `polluted-interleave` 是启发式风险带（公报源 + 公报排版标记 + 窄栏切片），**无法区分「窄栏但顺序正确」与「窄栏且串行交错」**；逐行是否真污染须人工对照官方文本。
