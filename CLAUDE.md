# CLAUDE.md — 台风态势实验室 · Claude Code 接力指南

一句话定位：官方真实数据 + 可解释的参数化物理推演 + 教学级可视化。
不做预报（《气象法》红线）：预测内容只转发官方机构；自研推演一律标
「情景模拟」；页脚免责声明与 fixture 红色角标**任何情况下不许删**。

## 架构

```
数据管道  fetch_typhoon.py（纯标准库单文件）
          zjwater 主源 → nmc 备源 → fixture 兜底；增量合并进 data/archive/
前端      web/index.html（单页）+ web/physics.js（Holland/集合/锥/CPA 纯函数）
          + web/vendor/maplibre-gl（本地 vendor，无外网依赖）
调度      .github/workflows/fetch.yml（每小时，有变更才提交）
工具      scripts/import_digital_typhoon.py（JMA GeoJSON → schema，兼 M4 回填）
          scripts/backfill_dt.py（DT 全库批量下载，本地跑）
          scripts/build_standalone.py（全内联单文件预览）
测试      tests/test_parse.py（18 断言）tests/test_physics.mjs（13 断言）
```

## 已确证事实（2026-07-17，沙盒实测 + 在产项目双重对照）

改任何解析代码前先看这张表，测试已把它们钉死：

1. `GET /Api/TyhoonActivity` 实测 200（站方拼写就是少个 p）；返回的 `time`
   是 **UTC ISO**（`2026-07-14T09:00:00.000+00:00`）。
2. `TyphoonInfo` 的 `points[].time / forecastpoints[].time` 是**北京时间字符串**
   （`2026-07-07 08:00:00`）。两种格式 `norm_time()` 都吃，别改坏。
3. **风圈四段顺序：`东北|东南|西北|西南`**（ne, se, nw, sw）。
4. `movedirection` 是旧式方位命名（`西北西`=WNW），DIR16 已含两套。
5. 预报点 `speed/pressure` 为 `"0"` 表示缺测；`ybsj` 是预报发布时间（ISO UTC）。
6. tfid 常规 6 位 `YYYYNN`，个别低压 8 位。
7. nmc 为 JSONP 裸数组：`list_{year}` 行 `t[0]`=dbid、`t[3]`=短编号 `YYNN`；
   `view_{dbid}` 点位偏移见 `NmcAdapter` docstring。机构码 BABJ→CMA 等。
8. Referer 实测非必需，但管道仍带（历史习惯，无害）。

## 命令速查

```bash
python3 fetch_typhoon.py                  # 真实抓取（auto：zjwater→nmc）
python3 fetch_typhoon.py --source fixture # 离线演示数据
python3 tests/test_parse.py && node tests/test_physics.mjs   # 全部测试
python3 -m http.server 8000               # 根目录起服务 → /web/
python3 scripts/build_standalone.py       # 单文件预览
python3 scripts/backfill_dt.py --from 2020 --to 2026 --convert  # 历史回填
```

## 任务队列（按序执行，每项有验收标准）

### T1 真实抓取闭环（最高优先，10 分钟）
跑 `python3 fetch_typhoon.py` 两次。
验收：`data/archive/2026/` 落盘；第二次跑完点数不翻倍；
`data/latest.json` 的 source=zjwater；起 http.server 后页面显示
真实台风、风圈形状与 typhoon.slt.zj.gov.cn 官网图**方向一致**
（不一致 = 象限映射出问题，报 issue 别硬改）。
顺手抽查 nmc：`python3 fetch_typhoon.py --source nmc`，
失败就按适配器 docstring 对照 `view_` 真实响应校偏移。

### T2 部署
GitHub 私有→公开仓库；Actions 启用（若境外 runner 连不上 zjwater，
按 workflow 尾注切本机 crontab 或腾讯云函数）；页面托管选
腾讯云 EdgeOne Pages 或 COS+CDN（境内可达优先）；自定义域名需 ICP 备案，先提。
验收：外网可访问、Actions 连续 24h 绿灯、数据自动更新。

### T3 JMA 实时适配器（M2）
目标：`www.jma.go.jp/bosai/typhoon/data/` 下的 JSON（先 curl `targetTc.json`
摸清单，再摸单台风 forecast/specifications 结构——**别凭记忆写偏移，
先抓真实响应**，教训在 git log 里）。产出 `JmaAdapter` + 测试样本 + 断言。
验收：latest.json 可含 JMA 预报，前端机构开关出现 JMA。

### T4 历史库（M4）
`backfill_dt.py --from 2015 --to 2026 --convert`；生成年度索引；
前端加历史台风选择器（读 index.json，回放任意台风）。
验收：淡季打开页面能选历史台风回放，含 2024 摩羯、2021 烟花。

### T5 物理层增强（M3+）
- 粒子层 2D canvas → WebGL（参考 earth.nullschool 思路），移动端 60fps
- B 参数换 Vickery–Wadhera (2008)，RMW fallback 换 Knaff–Zehr，注明文献
- 引导气流：Open-Meteo 拉 500hPa 风场，叠加进 windAt 背景项，
  集合扰动从纯统计升级为「引导气流积分 + 扰动」
验收：物理测试扩到 20+ 断言，参数出处在代码注释里可溯源。

### T6 性能与移动端
真机测：粒子帧率、图层加载、1.29MB standalone 首屏。降级策略落地。

### T7 文章素材（Juejin）
叙事主线就是这仓库的 git 历史：三处假定被真实证据推翻
（象限顺序、时间格式、NMC 偏移）——「跑通≠跑对」的现成案例。
截图留：TyhoonActivity 真实响应、测试红转绿、风圈对照官网图。

## 约束（用户风格，不可违背）

- 单文件偏好：管道保持单文件；前端 index.html + physics.js 两文件封顶
- 交付物零 AI 痕迹：注释与文案不写套话，不加 emoji
- fixture 数据必须红色角标明示；真实数据必须标来源与更新时间
- 免责声明不许删；城市影响必须带「几何计算非预警」字样

## 沙盒未验证项（诚实清单）

- `TyphoonInfo/{tfid}` 全量与 `TyphoonList/{year}`：形态经文档+在产项目确证，
  但沙盒 URL 白名单拦截，未直连实测 → T1 首跑覆盖
- nmc 实跑（偏移已对照在产代码写死，仍建议抽查）
- Actions 境外 runner → zjwater 连通性
- 移动端真机性能
