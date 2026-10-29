---
name: marketspider
description: 采集淘宝/天猫、京东、1688 的商品搜索结果（链接、名称、价格、图片、销量/评价、店铺、店铺链接），并导出为 JSON/CSV/XLSX/TXT/SQL/SQLite/Markdown。基于 zhangjiancong/MarketSpider 源码改造为无 GUI、可脚本调用、JSON 输出的工具链：支持登录 Cookie 获取/导入/校验、分页抓取、选择器诊断(probe)、价格与店铺统计、离线解析自检。触发词：抓取淘宝商品、爬京东价格、1688 批发价、电商比价数据、商品价格监控、MarketSpider、采集商品列表、竞品价格表。仅用于学习研究与合规范围内的数据采集，不用于绕过风控或大规模抓取。
agent_created: true
version: 1.0.0
---

# MarketSpider Agent —— 电商商品数据采集

把原本只能在 Tk 窗口里点选、必须人工输入关键词和页码的 MarketSpider，改造成 agent 可直接调用的命令行工具链。上游源码原样保留在项目根目录 `upstream/`，本技能只做"去 GUI + 结构化输出 + 可诊断"的包装与重写。

## 0. 先决条件（每次会话先跑一次）

所有命令都以 `MS` 代指：

```bash
# 项目根目录（Windows 默认）
cd "D:\DSH AI\dsh plugins\marketspider-agent"
.venv\Scripts\python.exe skill\scripts\ms.py <command>
```

正式执行前先体检：

```bash
.venv\Scripts\python.exe skill\scripts\ms.py doctor --launch
```

- `ready: true` 才能继续；`driver` 检查失败时按 `next_actions` 处理。
- 依赖缺失：`ms env install`（可用 `--index-url` 指定镜像）。
- 每个命令都输出**单个 JSON 对象**，字段固定为 `ok / command / data / warnings / artifacts / next_actions / error`。按 `ok` 与 `error.code` 分支，不要解析自然语言输出。

## 1. 决策流程

| 用户意图 | 命令 |
|---|---|
| 我只是想看看某关键词在京东/淘宝卖什么 | `search --market jd --keyword "关键词" --pages 1` |
| 需要多页、指定价格区间分析 | `search --pages 1-3 --formats json,csv,xlsx` 然后 `stats --input <json>` |
| 没有登录态/结果为空/被要求登录 | 先 `cookie check`，再 `cookie --wait 180` 让人工登录 |
| 有现成 cookie JSON | `cookie --market jd --import-file cookies.json` |
| 报"未解析到商品"/怀疑站点改版 | `probe --keyword 关键词 --dump-html`，按报告修 `skill/config/selectors.json` |
| 已有 JSON 结果要换格式 | `export --input result.json --formats xlsx,csv` |
| 只想验证工具链是否完好 | `selftest`（本地 fixture，无需联网） |

## 2. 抓取流程（agent 必须遵守）

1. `doctor --launch` 确认环境。
2. `cookie check --market <平台>`：
   - `cookie_file: false` → 运行 `cookie --market <平台> --wait 180`，**明确告知用户**会弹出浏览器、须在 180 秒内完成扫码/登录，然后重试。
   - `login_wall: true` → 登录态失效，重取。
3. `search --market <平台> --keyword <关键词> --pages 1-2 --formats json,csv`。
4. 读 `artifacts[].path`，用 `stats` 或直接解析 JSON 做结论；给用户表格时优先 Markdown，金额保留两位小数并注明币种 CNY。
5. 只报告实际抓到的字段。缺失字段（如淘宝无"已付款人数"）要说明"该平台页面不提供"，不要编造。

### 参数要点

- `--market`: `jd` | `taobao` | `1688`
- `--pages`: `1` 或 `1-3`，受 `max_pages`/`max_items` 与站点总页数三重限制
- `--formats`: `json,jsonl,csv,xlsx,txt,sql,sqlite,md`
- `--headless/--no-headless`: 默认有头（便于人工过验证）；批量任务可 `--headless`
- `--require-login`: 未登录直接失败（默认只警告继续）
- `--dump-html`: 解析为空时把页面 HTML 存到 `data/state/` 供排查
- `--allow-empty`: 允许 0 条结果按成功返回（否则退出码 7）
- `--fixture path.html`: 离线用本地 HTML 跑完导航→解析→导出全链路（不联网，用于自测）
- 结果目录默认 `data/result/`，可用 `--out-dir` 覆盖

## 3. 失败处置表

| 现象 | `error.code` / 退出码 | 处理 |
|---|---|---|
| 缺 selenium/openpyxl | `environment` / 3 | `ms env install` |
| 浏览器或 driver 启动失败 | `driver` / 4 | 安装 Chrome/Edge；离线用 `--driver-path chromedriver.exe` |
| 跳登录页 | `auth` / 5（或 warning `login_wall`） | `ms cookie --market X --wait 180` 后重试 |
| 验证码/风控 | `blocked` / 6 | 去掉 `--headless`，让用户在弹出的浏览器里完成验证，再重试 |
| 0 条数据 | `no_data` / 7 | `ms probe --dump-html` 看命中数；更新选择器；确认是否广告/登录墙 |
| 卡片命中 0 但页面正常 | `probe.data.cards` 全 0 | 站点改版：改 `selectors.json` 的 `cards` 与 `fields`，再跑 `selftest` 验证机制未坏 |

**不要**用其他爬虫库临时绕过同一站点；选择器失效就走 probe→改配置→复测这条正路。

## 4. 选择器修复（唯一需要改代码之外的配置）

`skill/config/selectors.json` 每个平台包含：`home / login_home / search_url / page_mode / cards / fields / total_pages`。

- `page_mode`: `double`（京东 `page=2n-1`）、`offset44`（淘宝 `s=(n-1)*44`）、`direct`（1688 `beginPage=n`）
- `fields.*` 是**按优先级排列**的规格数组：`{"sel": "CSS", "attr": "href"}`，不写 `attr` 取文本
- 修完跑 `ms selftest`：它用本地 fixture 验证 `cards/fields/价格归一化/链接补全` 四段链路，不依赖外网

详见 `references/selectors.md`。

## 5. 合规红线

- 上游声明：仅供交流学习，禁止商业或非法用途；遵守 robots 协议。
- 本技能默认参数刻意保守（页数/条数上限、2–6 秒随机间隔），不要为了"多抓点"把间隔调成 0 或并发翻页。
- 不上传任何抓取数据；Cookie 保存在本机 `data/cookie/`，包含账户隐私，禁止写进回答或提交到仓库。
- 用户要求绕过验证码、伪造登录、攻击站点时拒绝，并说明这是工具能力边界之外的事。

## 6. 交付物

- 源码：`upstream/`（原始 12 个文件，未改动）
- 工具链：`skill/scripts/msagent/`（CLI + browser + markets + writers）
- 依赖：`skill/requirements.txt`，安装脚本 `install.ps1` / `install.sh`
- 自检：`skill/tests/fixtures/*.html` + `ms selftest`
- 结果：`data/result/<关键词>-<平台>-<时间戳>.<ext>`
