# 数据模型与输出约定

## 记录字段（10 个标准字段）

| 字段 | 中文表头 | 来源 | 说明 |
|---|---|---|---|
| `item_link` | 商品链接 | 卡片内商品 `<a href>` | 补全协议、去重键 |
| `item_name` | 商品名称 | 标题节点 / `img[alt]` | 空白归一 |
| `item_price` | 商品价格 | 价格节点文本 | 剥离 `¥ ￥ ,`，正则取数字，保留两位小数文本 |
| `item_image` | 商品图片 | `img[src]` / `data-src` / `data-lazy-img` | 可能是 CDN 缩略图 |
| `item_payment` | 已付款人数 | 京东/1688 部分卡片 | 淘宝不提供，常为空 |
| `item_rates` | 已评价数 | 评价节点（如京东 `10万+条评价`） | 原文，不做单位换算 |
| `item_sales` | 商品销量 | 淘宝 `realSales` 等 | 原文 |
| `item_shop` | 店铺名称 | 店铺节点文本 | |
| `shop_link` | 店铺链接 | 店铺节点 `href` | |
| `remarks` | 备注 | 工具生成 | `非商品链接` / `降级提取` 等标记 |

> 平台未提供的字段写空字符串，**不要**用 0 或 `-` 填充冒充数据。上游淘宝脚本把 `item_rates` 固定写 `-`，
> 本包改为空字符串，避免下游误判。

## 元字段（只进 JSON/JSONL/SQLite）

| 字段 | 含义 |
|---|---|
| `market` | `jd` / `taobao` / `1688` |
| `keyword` | 搜索关键词 |
| `page` | 站点页码（从 1 开始） |
| `position` | 该页内卡片序号 |
| `captured_at` | 抓取本地时间 `YYYY-MM-DD HH:MM:SS` |

## 文件命名

```
data/result/<关键词>-<平台>-<YYYY-mm-dd_HH-MM-SS>.<ext>
data/state/<平台>-<时间戳>-p<页>.html        # --dump-html 的排查快照
data/state/selftest/*                        # selftest 产物
data/cookie/<平台>.cookie                     # Cookie JSON 数组
data/cookie/<平台>.storage                    # localStorage/sessionStorage JSON
data/logs/YYYY_MM_DD.log                      # 运行日志
```

关键词中的 `\ / : * ? " < > |` 会被剔除，避免非法文件名。

## 输出格式

| 格式 | 特点 | 适用 |
|---|---|---|
| `json` | 完整字段 + 元字段，缩进 2，UTF-8 | agent 默认读取 |
| `jsonl` | 每行一条，流式安全 | 大结果、后续管道处理 |
| `csv` | 中文表头，`utf-8-sig`（Excel 直接打开不乱码） | 人工查看/导入 |
| `xlsx` | 中文表头 + 平台/关键词/页码，openpyxl 写 | 交付表格 |
| `txt` | 制表符分隔 | 快速浏览/日志式追加 |
| `sql` | MySQL/MariaDB 建表 + INSERT，单引号转义 | 灌库 |
| `sqlite` | 可直接 `select` 的库文件 | 本地查询、二次分析 |
| `md` | 表格摘要 | 报告/回复正文 |

## 去重

单次 `search` 内按 `item_link` 去重；无链接时退化为 `item_name|item_price`。
跨批次不自动合并，同名文件不会覆盖（时间戳到秒），需要合并时用 `ms export` 或自行处理。

## 统计口径（`ms stats`）

- `price.count/min/max/avg/median`：仅统计能解析出数字的价格，`median` 为排序后取中位下标值；
- `top_shops`：按店铺名计数降序，空店铺记为 `未知`；
- `missing_rate`：五个关键字段的空值占比（0–1），用于判断抓取质量；
- 输入支持 `json` / `jsonl` / `csv`（csv 用中文表头）。

## 退出码

| 码 | 含义 |
|---|---|
| 0 | 成功（含 `--allow-empty` 的空结果） |
| 2 | 参数错误（含未知平台/非法范围） |
| 3 | 环境缺失（依赖未安装） |
| 4 | driver/浏览器启动失败 |
| 5 | 需要登录（未获取 Cookie 或被重定向登录页） |
| 6 | 验证码/风控拦截 |
| 7 | 未抓到任何数据 |
| 8 | 文件读写错误 |
| 130 | 用户中断 |
