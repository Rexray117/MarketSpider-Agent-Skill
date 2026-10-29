# MarketSpider Agent · 电商商品数据采集

[![DSH Skill](https://img.shields.io/badge/DeepSeek%20Harness-skill-4a7dff)](https://github.com/Rexray117/MarketSpider-Agent-Skill)
[![version](https://img.shields.io/badge/version-1.0.0-brightgreen)](https://github.com/Rexray117/MarketSpider-Agent-Skill/releases)
[![license](https://img.shields.io/badge/license-MIT-blue)](./LICENSE)

> 一个 **DeepSeek Harness（DSH）/ Agent Skill**：`marketspider` v1.0.0。

> 上游说明：基于 zhangjiancong/MarketSpider 源码改造为无 GUI、可脚本调用的工具链。

采集淘宝/天猫、京东、1688 的商品搜索结果（链接、名称、价格、图片、销量/评价、店铺、店铺链接），并导出为 JSON/CSV/XLSX/TXT/SQL/SQLite/Markdown。基于 zhangjiancong/MarketSpider 源码改造为无 GUI、可脚本调用、JSON 输出的工具链：支持登录 Cookie 获取/导入/校验、分页抓取、选择器诊断(probe)、价格与店铺统计、离线解析自检。触发词：抓取淘宝商品、爬京东价格、1688 批发价、电商比价数据、商品价格监控、MarketSpider、采集商品列表、竞品价格表。仅用于学习研究与合规范围内的数据采集，不用于绕过风控或大规模抓取。

## 适用场景

把本仓库整体放进 agent 的技能目录即可按描述自动触发；也可以在对话里直接点名 `marketspider`。

## 目录结构

```
`SKILL.md`
`requirements.txt`
`LICENSE`
`.gitignore`
config/
references/
scripts/
tests/
```

## 安装

### DSH（DeepSeek Harness）

技能目录放到用户级技能根：

```powershell
# Windows
git clone https://github.com/Rexray117/MarketSpider-Agent-Skill.git "$env:USERPROFILE\.dsh\skills\marketspider"
```

```bash
# macOS / Linux
git clone https://github.com/Rexray117/MarketSpider-Agent-Skill.git ~/.dsh/skills/marketspider
```

也可以用 DSH 的插件管理安装本地目录：

```
plugin_manager action=install_bundle target=<本仓库绝对路径>
```

### 其他 agent（Codex / WorkBuddy / Claude Code）

技能根分别位于 `~/.codex/skills`、`~/.workbuddy/skills`、`~/.claude/skills`，把本仓库克隆成其中的 `marketspider` 子目录即可（技能名以 `SKILL.md` 的 `name: marketspider` 为准）。

## 版本

- 当前版本：**v1.0.0**（与 `SKILL.md` 的 `version` 字段一致）
- 变更记录见仓库提交历史

## License

MIT © 2026 Rexray117
