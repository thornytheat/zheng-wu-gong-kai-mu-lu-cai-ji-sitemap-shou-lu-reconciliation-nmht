# crawlscope · 政务公开目录采集与 sitemap 收录对账

市政府网站编辑部数据组每天要回答两件事：**信息公开目录里每条都进 sitemap 了吗**、**列表分页抓全了吗**。
crawlscope 顺序抓取目录列表、做 URL 规范化、判定分页边界、把目录集合和 sitemap 集合对账，并导出差异清单。

## 能力

- **站点配置**：保存目标站（SQLite `sites` 表，seed 自 `sample_data/site_profiles.json`），工作台可切换。
- **采集执行**：从列表第 1 页顺序抓取，超时 5 秒、失败重试 1 次，只保留同站链接。
- **分页诊断**：记录每页条目数 / 新增数 / 是否重复页，给出终止原因与停在第几页。
- **URL 规范化**：去 fragment、去 `utm_*`/`spm`/`from`、query 键排序、相对转绝对、host 小写、去默认端口、合并重复斜杠、去尾部斜杠（根路径除外）；保留已有百分号编码，不二次编码；路径大小写敏感。
- **sitemap 对账**：支持 `sitemapindex` 递归汇总全部子文件；逐文件登记 **成功 / 空文件 / 超时 / HTTP失败 / 解析失败** 状态；同一链接出现在多个文件时按 lastmod 更新时间裁决并保留全部出处。全部文件读取成功才一次性发布 `only_in_catalog`（目录有、sitemap 缺）、`only_in_sitemap`（sitemap 多出）、`lastmod_mismatch`（日期不一致）三类差异；读取不完整时只展示待核对范围，结论为「待核对」，绝不判收录完整。
- **差异导出**：三类差异 + 全部失败条目导出 UTF-8-BOM CSV；读取不完整时 CSV 只含文件状态与待核对范围。

## 核心规则

1. **URL 规范化**：纯函数在 `app/urls.py`，`normalize_with_evidence()` 同时返回变更依据（页面「规范化示例对照」直接展示）。
2. **分页边界**：`app/crawler.py:crawl()` 顺序抓取，终止判定顺序为
   ① 本页提取条目为 0（空页）→ ② 本页同站 URL 集合与上一页完全相同（重复页）→ ③ 连续 2 页无新增 URL；
   另外命中「最大页数」「分页链接缺失」「列表页抓取失败」也会停止并写明原因。
   页码由页内分页条（`a[data-page]`）确定，不猜 URL。
   重复页有两种策略：`tolerant`（默认，记录重复页后继续抓完）与 `strict`（命中即停）。
3. **同站过滤**：host 不等于目标 host 的链接从目录剔除，计入 `外站` 失败分类。
4. **对账**：`app/reconcile.py` 纯函数，lastmod 按 `YYYY-MM-DD` 比较，日期缺失不计入不一致；跨文件合并在 `merge_sitemap_files()`，可解析日期/时间戳优先于纯文本、空值最旧，lastmod 相同取读取顺序更早的文件，结果可复现。
5. **多子文件读取**：`app/sitemap_loader.py:load_sitemaps()` 深度优先递归 `sitemapindex`（最深 5 层、循环引用跳过），任一子文件失败都不影响其他文件，`complete` 仅在全部文件为「成功/空文件」时为真。
6. **失败分类**：4xx / 5xx / 超时 / 外站 / 重复页，每类计数 + 至多 3 条示例 URL；超时与 5xx 会重试 1 次。

## 启动

```bash
cd batch-20261114b/projects/gb-621
cp .env.example .env          # 可选
docker compose up -d --build  # app: http://127.0.0.1:18210  样例站: http://127.0.0.1:19210
docker compose ps
curl -s http://127.0.0.1:18210/health
```

- 主应用通过 compose 内网 `http://sample-site` 访问样例站，**不要**改成宿主机 localhost。
- 数据卷 `crawlscope-data` 保存 SQLite。重建数据库：`docker compose down -v` 后再 `up`。
- 同一目标站 + 最大页数 + 重复页策略重复扫描时按 `scan_key` 就地更新，`run_count` 递增，不会产生重复扫描行。

## 演示步骤

1. 首页 填目标站 `http://sample-site`、最大页数 10 → 进入工作台 → 执行采集。
2. 默认「完整核对」策略会抓完 6 页：第 3 页命中重复页（与第 2 页完全相同），第 6 页为空页终止。
3. 结果页：结论、分页明细、sitemap 文件明细（含跨文件重复链接裁决）、三类差异、失败分类、URL 规范化示例；目录 25 条中 3 条未进 sitemap、sitemap 多出 2 条、1 条 lastmod 不一致、3 条外站、1 条 404、1 个重复页。
4. 结果页右侧「用严格策略重跑」可验证严格模式：终止原因=重复页、停在第 3 页。
5. 子文件超时/损坏的演示：让任一 `sitemap/sitemap-*.xml` 返回超时或非法 XML 后重跑，结论变为「待核对」，只展示待核对范围与各文件状态，差异计数全部为 0；恢复后重跑同一扫描即发布最终差异（幂等更新、结果不变）。
6. `/review` 看历史扫描并下载差异 CSV，可用「待核对」筛选读取不完整的扫描。

## API

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/health` | 健康检查 |
| GET | `/`、`/workbench`、`/result/{scan_id}`、`/review` | 四个页面 |
| POST | `/api/scan` | `{"target":"http://sample-site","max_pages":10,"policy":"tolerant"}` → scan_id + summary；非法 URL 返回 400，页数越界 422 |
| GET | `/api/scan/{scan_id}` | 完整结果 JSON |
| POST | `/api/normalize` | `{"url":"./x.html?utm_source=a#t","base":"http://sample-site/list/page-2.html"}` |
| GET | `/review/{scan_id}/diff.csv` | 差异 CSV 下载 |

## 目录

```text
app/
  main.py       路由（薄）
  settings.py   常量与 DB 路径
  urls.py       URL 规范化（纯函数）
  crawler.py    抓取、分页边界、失败分类
  reconcile.py      sitemap 解析（urlset/sitemapindex）、跨文件合并裁决与对账（纯函数）
  sitemap_loader.py 递归抓取 sitemapindex 与全部子文件，逐文件登记读取状态
  scan.py       采集 → 详情校验 → sitemap 汇总 → 对账编排
  store.py      SQLite（sites / scans，幂等写入，含旧库加列迁移）
  export.py     CSV 导出
  templates/    base / index / workbench / result / review
  static/       app.js、style.css（原生，无框架/CDN）
sample_site/    nginx 样例站（6 页目录 + 24 个详情页 + sitemapindex 与 3 个子 sitemap）
sample_data/    站点配置 seed
tests/          test_urls / test_pagination / test_reconcile / test_api
```

## 已知限制

- sitemapindex 递归最深 5 层，循环引用的文件登记为「未读取」并跳过（本次读取判不完整）；嵌套索引与 urlset 之外的文档类型不解析。
- 空文件指 HTTP 200 但 urlset/索引内没有条目，算读取成功、零条参与合并；空响应体或非法 XML 记为「解析失败」。
- 分页依赖页内分页条 `a[data-page]` 或同页相对 `page-N.html` 链接，不跟随「下一页」按钮文本。
- 采集是同步请求，站点很大时页面会等待；单次上限 30 页。
- 样例站第 3 页是第 2 页的字节级副本（含相同分页条），因此重复页判定与页码推进都基于第 1 页的分页条。
