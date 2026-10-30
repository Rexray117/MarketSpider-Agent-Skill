# 上游源码分析与改造映射

上游：<https://github.com/zhangjiancong/MarketSpider>（MIT，README 声明仅供交流学习）。
本机下载方式：GitHub 直连被重置，改用 `https://codeload.github.com/zhangjiancong/MarketSpider/zip/refs/heads/main`。
源码原样保留在 `upstream/`，未做任何修改。

## 上游文件清单与真实状态

| 文件 | 上游版本 | 作用 | 直接可给 agent 用吗 |
|---|---|---|---|
| `Core.py` | 2.0-beta1 | Tk GUI、浏览器控制、Cookie 注入、5 种 Writer、日志 | 否：`Gui` 在 `import` 时创建 `tkinter.Tk()`，且 `Logger` 用相对路径 `logs\` |
| `Starter.py` | 1.0 | 顶层 `while` 菜单 + `input()`，负责配置/依赖修复/启动各爬虫 | 否：交互菜单、`match` 语法需 3.10+ |
| `GetCookie.py` | 2.0 | 打开商城登录页，倒计时 60 秒后保存 cookie/storage | 否：写死 60 秒、`ask_string` 弹窗、`sys.exit` |
| `Spider_jd.py` | 2.0.0 | 京东搜索抓取 | 否：顶层脚本、必填弹窗、`input()` 结束 |
| `Spider_taobao.py` | 2.0.0 | 淘宝搜索抓取 | 否：同上，且选择器含构建期哈希 |
| `1688Spider.py` | 1.0 | 1688 搜索抓取 | 否：`from functions.marketSpider import *`，该模块**不在仓库里**，脚本必然 ImportError |
| `Update.py` | 1.1 | 从 GitHub Pages 下载更新自身组件 | 否：Tk 更新器；对 agent 无意义且有覆盖源码风险 |
| `requirements.txt` | - | playsound / selenium / requests / openpyxl | 部分：`playsound` 仅 Windows 且上游已停更，本包不装 |
| `error.wav`, `pages/version.json`, `docs/images/*` | - | 提示音、更新源、教程截图 | 否：运行期资产，原样保留 |

### 结构性阻塞点（为什么不能直接调用）

1. **导入即弹窗**：`Core.Gui.__init__` 立刻创建 Tk 根窗口；`Spider_*.py` 在模块顶层执行全部逻辑，
   `import` 就等于运行，无法被脚本安全调用。
2. **相对路径绑定 cwd**：`config.json`、`cookie\`、`result\`、`logs\` 全部相对当前目录，
   agent 换个工作目录就找不到数据。
3. **阻塞式人机交互**：`ask_string` 等待窗口输入、`setTimeout` 秒级倒计时、`input("---Error---")`，
   在无人值守环境会永久挂住。
4. **错误处理靠弹窗**：`show_error_box` 用 `messagebox`，无退出码、无结构化输出。
5. **Windows 路径写死**：`f"cookie\\{market}.cookie"`、`f"logs\\{date}.log"`，跨平台不可用。
6. **`1688Spider.py` 缺依赖模块**，上游已不可运行。

## 改造映射

| 上游能力 | 新位置 | 说明 |
|---|---|---|
| `Core.BrowserControl` | `scripts/msagent/browser.py` → `BrowserSession` | 保留 Chrome/Edge/Firefox、`--disable-blink-features=AutomationControlled`；新增 headless、临时 profile、CDP `navigator.webdriver` 隐藏、页面就绪等待、滚动 |
| `Core.inject_cookie` / `save_cookie` | `BrowserSession.load_cookie/save_cookie` | 同样保存 `.cookie` + `.storage`（localStorage/sessionStorage），但路径固定在数据目录 |
| `Core.CsvWriter/JsonWriter/ExcelWriter/TxtWriter/SqlWriter` | `scripts/msagent/writers.py` | 保留 10 字段与中文表头；新增 jsonl / sqlite / md；JSON 额外记录 market/keyword/page/position/captured_at |
| `Core.Logger` | `scripts/msagent/logutil.py` → `RunLog` | 纯文件日志 + 结构化 warnings，不弹窗 |
| `Core.check_configFile_exist/pre_check_cookie` | `scripts/msagent/config.py` + CLI | 缺失不再阻断，降级为 warning 与 `next_actions` |
| `Spider_jd.py` 的选择器 | `config/selectors.json` → `jd` | 选择器外置，probe 可诊断 |
| `Spider_taobao.py` | `selectors.json` → `taobao` | 用 `[class*=]` 前缀 + 商品链接正则兜底 |
| `1688Spider.py` | `selectors.json` → `1688` | 独立重写，去掉缺失模块依赖 |
| `GetCookie.py` 的 60 秒倒计时 | `ms cookie --wait N` | 轮询登录 cookie 标记（`pin`/`_tb_token_` 等），命中即存 |
| `Starter.py` 的菜单 | CLI 子命令 | `doctor/env/config/markets/cookie/search/probe/export/stats/selftest` |
| `Update.py` | 不迁移 | 自更新会覆盖已改造代码，且依赖 GitHub Pages |

## 保留与取舍

- **保留**：登录 Cookie 复用思路、10 字段数据结构、中文表头、随机翻页延时、验证码时暂停的语义。
- **移除**：Tk 窗口、提示音 `playsound`、`input()` 收尾、`sys.exit` 硬退出、GitHub Pages 自更新。
- **新增**：JSON 信封 + 稳定退出码、9 种输出格式、去重、probe 诊断、离线 fixture 自检、
  跨平台路径、依赖自检与一键安装。

## 已知局限（不是 bug）

- 淘宝/1688 的商品 class 会随前端发版变化，选择器需要持续维护；
- 京东对自动化与异地 IP 的风控可能导致匿名访问被重定向到登录页；
- 无验证码识别能力（刻意不做），遇到验证码只提示人工处理；
- 不实现并发翻页与代理池，避免对站点造成压力。
