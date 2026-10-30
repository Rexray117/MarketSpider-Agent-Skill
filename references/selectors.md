# 选择器包说明（selectors.json）

文件位置：`config/selectors.json`（相对技能内容根目录；解压包里是 `<安装包根>/skill/config/selectors.json`，GitHub/DSH 安装后是仓库根的 `config/selectors.json`）。三个平台共用一套 schema，改这个文件不需要动 Python 代码。

## 结构

```jsonc
{
  "jd": {
    "label": "京东",
    "home": "https://www.jd.com",                 // 注入 Cookie 前先访问的域
    "login_home": "https://www.jd.com",           // cookie 获取命令打开的登录页
    "search_url": "https://search.jd.com/Search?keyword={kw}&enc=utf-8&page={page_param}",
    "page_mode": "double",                        // 页码换算方式，见下表
    "item_link_pattern": "item\\.jd\\.com",       // 商品链接正则，用于降级扫描/广告标记
    "cards": ["#J_goodsList > ul > li"],          // 卡片选择器，按优先级尝试
    "link_fallback": ["a[href*='item.jd.com']"],  // 卡片内找不到链接时的兜底
    "total_pages": ["#J_topPage > span > i"],     // 总页数文本选择器
    "fields": {
      "item_link": [
        {"sel": "div.p-img > a", "attr": "href"}, // attr 省略则取可见文本
        {"sel": "a[href*='item.jd.com']", "attr": "href"}
      ]
      // item_name / item_price / item_image / item_payment / item_rates /
      // item_sales / item_shop / shop_link 同上
    }
  }
}
```

字段规格按数组顺序尝试，**第一个非空值胜出**。文本会做空白归一；价格会剥离 `¥ ￥ ,` 并正则取数字。

## page_mode

| 值 | 含义 | 典型站点 |
|---|---|---|
| `double` | `page_param = 2*n - 1` | 京东 `page=` |
| `offset44` | `page_param = (n-1)*44` | 淘宝 `s=` |
| `offset60` | `page_param = (n-1)*60` | 预留 |
| `direct` | `page_param = n` | 1688 `beginPage=` |

## 修复流程（站点改版时）

1. 打开有头浏览器复现：

   ```bash
   ms probe --market jd --keyword 蓝牙耳机 --dump-html
   ```

2. 看 `data.cards`：哪个选择器命中了、命中多少。全 0 说明 `cards` 失效。
3. 看 `data.fields`：形如 `"item_price": "1/5"` 表示前 5 张卡片里只有 1 张取到价格。
   用 `data.sample` 对照真实字段值，确认没有取错。
4. 用 `data.html_dump` 的 HTML 在浏览器 F12 里重写选择器（优先用稳定的 `data-*`、语义 class、
   `[href*='item.xxx.com']`，避免构建期哈希 class）。
5. 改 `selectors.json` → 跑 `ms selftest`（本地 fixture 不动）→ 再 `probe` 复核 → `search`。

> fixture 自检通过但 probe 仍不命中，说明是"选择器对真实 DOM 失效"，不是工具链坏了。

## 各平台现状

- **京东**：卡片结构多年稳定（`#J_goodsList li.gl-item`）。但自动化/异地 IP 常被重定向到
  `passport.jd.com` 登录页，此时命令以 `auth`/退出码 5 退出 —— 先取 Cookie。
- **淘宝**：卡片 class 是构建期哈希（`Card--xxxx`、`priceWrapper--xxxx`），本包用
  `[class*='...']` 前缀匹配降低失效率。淘宝强依赖登录态，匿名基本拿不到结果。
- **1688**：同样使用哈希 class（`mojar-element-*` 相对稳定）。上游 `1688Spider.py` 依赖的
  `functions.marketSpider` 模块并未随仓库发布，本包是独立重写，不依赖该缺失模块。

## 不要做的事

- 不要在 `cards` 里写 `div`、`a` 这类过宽选择器来"先跑通"，会把导航/广告当商品。
- 不要把已验证失败的选择器留着当唯一选项 —— 追加新规格而不是覆盖旧的，等 probe 确认后清理。
