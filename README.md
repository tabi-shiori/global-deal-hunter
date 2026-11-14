# GLOBAL DEAL HUNTER｜全球羊毛情报中心

24 小时全球优惠、免费资源、AI 订阅折扣、Crypto 空投积分、海外银行 / U 卡福利与免费云资源自动化情报工作空间。

## 空间目录结构

- `scripts/db.py`：SQLite 主数据库模型、URL 规范化与 SHA-256 指纹去重、自动截止日期提取、地区/门槛/隐藏条件识别、0–100 性价比评分与过期自动归档引擎。
- `scripts/seed_verified_deals.py`：经官方页面逐项核实的全球优惠初始情报库与 20 个高信噪比监控信源。
- `scripts/collector.py`：每 2 小时自动巡检器（支持 `--daemon --interval 7200` 持续运行或单次触发），自动并发探活官方链接、拉取公开 RSS/API 线索、拦截重复项、将过期活动移入 `H. 已结束活动档案`。
- `scripts/sync_gsheets.py`：Google Sheets 云端 10 分表主数据库实时同步器。
- `scripts/server.py`：本地全栈看板与 REST API 服务（默认端口 `8787`，内置 2 小时后台定时巡检线程）。
- `web/`：独立 Web 数据看板前端（含 A–H 八大模块、12 字段档案抽屉、防反撸风险高亮、智能验重录入与 CSV 导出）。
- `.github/workflows/deal-hunter-cron.yml`：GitHub Actions 云端 `0 */2 * * *` 无人值守定时任务配置。

## 快速命令

```bash
# 启动看板服务 + 内置每2小时自动巡检守护线程
python3 scripts/server.py

# 手动立即执行一次全网信源巡检与链接验活
python3 scripts/collector.py --mode manual_cli

# 同步最新数据至 Google Sheets 云端数据库
python3 scripts/sync_gsheets.py
```
