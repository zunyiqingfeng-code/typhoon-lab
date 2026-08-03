# 台风态势实验室（typhoon-lab）

官方真实数据 + 可解释的参数化物理推演 + 教学级可视化，外加一件别处没有的东西：
多机构预报的历史复盘。原则一条：假数据永远明示——fixture 模式页面右上角是红色
「演示数据」角标，真实源才显示 LIVE；所有预测内容转发官方机构并署名，自研推演标
「情景模拟」。

## 四个页面

- `web/index.html` 实时态势图：当前/最近台风的路径、风圈、多机构预报锥、
  Holland 风场粒子、集合路径、城市影响估算（几何计算非预警）；JMA 实时三级降级
  （浏览器直连 → Actions 同步镜像 → 本地档案），来源以角标明示
- `web/archive.html` 历史档案：2000–2026 全部 643 个台风，按年份/强度/寿命/路径
  长度筛选排序，地图回放 + 强度曲线 + 年度峰值对比
- `web/verify.html` 复盘台：选任一历史台风，把各机构当时发布的预报叠在实际路径上，
  连线即误差；拖时间轴倒回不同发布时刻。这是转发型可视化给不了的——它只讲会怎样，
  不讲谁说对了
- `web/trends.html` 27 年总榜：643 个台风、11 万余条历史预报聚合出的机构误差榜、
  误差随提前量增长、72h 误差逐年趋势、强度误差

## 目录

```
fetch_typhoon.py        数据管道，纯标准库单文件
                        zjwater 主源 / nmc 备源 / jma 第二意见源 / fixture 兜底
data/latest.json        前端读的活跃快照（每机构最新一份预报）
data/index.json         当年索引
data/archive/<年>/<编号>.json   逐台风全量归档，含每机构全部历史预报（schema 1.1）
data/archive/index.json 跨年主索引，复盘台/总榜的选择器读它
data/scores.json        跨年评分聚合（管道侧预计算，浏览器加载不了 110MB 全归档）
data/records.json       643 台风摘要（起止/路径长度/峰值强度等），档案页列表与排序用
data/realtime/jma_latest.json   JMA 实时镜像（Actions 每小时同步，直连失败时兜底）
web/index.html          实时态势图
web/archive.html        历史档案（新）
web/verify.html         复盘台
web/trends.html         27 年总榜（内联 SVG 图表，零依赖）
web/tracklib.js         共享库：强度色带/时间格式化/路径统计/抽稀/强度曲线图
web/physics.js          Holland 风场 / 集合 / 锥 / CPA 纯函数
web/eval.js             复盘评测层：预报点对实况位置算误差，纯函数
web/splash.js           雷达主题开屏动画，四页共用
web/vendor/             maplibre-gl，npm 官方发行文件原样落盘
web/assets/land_wp.json Natural Earth 50m 陆地，裁至西太
scripts/score_archive.mjs   跑遍全归档算跨年评分 → scores.json
scripts/build_records.py    扫全归档生成 records.json（归档页列表/排序/年度对比）
scripts/fetch_jma_live.py   JMA 实时抓取 → realtime/jma_latest.json（失败返回 0 不破坏旧文件）
scripts/ci_healthcheck.py   CI 趋势告警：台风数/轨迹点骤降即红灯
scripts/import_digital_typhoon.py  Digital Typhoon(JMA) GeoJSON 导入器
scripts/backfill_dt.py  DT 全库批量下载
scripts/build_standalone.py  打零依赖单文件预览（--page index|archive，数据/镜像内联）
tests/                  解析层(31)/物理层(21)/评测层(15) 断言与样本
.github/workflows/fetch.yml  每小时抓取+重建索引评分+JMA 镜像+records 重建+健康检查+有变更才提交
```

## 快速开始

```bash
python3 fetch_typhoon.py --source fixture   # 离线：生成演示数据
python3 -m http.server 8000                 # 项目根起服务
# 浏览器打开 http://localhost:8000/web/          实时态势图
#                          /web/archive.html    历史档案
#                          /web/verify.html      复盘台
#                          /web/trends.html      27 年总榜

python3 fetch_typhoon.py                     # 真实抓取：zjwater 优先，失败切 nmc
python3 fetch_typhoon.py --source jma        # 日本气象厅直连（第二意见源）
python3 fetch_typhoon.py --backfill 2000-2026  # 回填历史（只写归档，带礼貌间隔）
python3 fetch_typhoon.py --reindex           # 重建跨年主索引
node scripts/score_archive.mjs               # 重算跨年评分 → data/scores.json
python3 scripts/fetch_jma_live.py            # JMA 实时 → data/realtime/jma_latest.json
python3 scripts/build_records.py             # 重建 records.json（归档页数据源）
python3 scripts/build_standalone.py --page index   # 单文件：台风态势_index_standalone.html
python3 scripts/build_standalone.py --page archive # 单文件：台风态势_archive_standalone.html

python3 tests/test_parse.py && node tests/test_physics.mjs && node tests/test_eval.mjs
```

## 数据与 schema

- schema 1.1：归档保全每机构全部历史发布预报（复盘评测的原料）；latest.json 仍每机构
  只吐最新一份，前端契约不变。单台风归档含全预报史后可达 ~1MB
- 归档现覆盖 2000–2026 共 643 个台风、11.2 万余条历史预报，约 110MB
- JMA 实时链路：浏览器直连 jma.go.jp（CORS 已实测放行）→ 失败用 Actions 同步的
  `data/realtime/jma_latest.json` 镜像 → 再失败退回本地档案。JMA 官方接口不含风速/
  风圈字段，相关指标位显示「—」并注明
- 三个数据源：zjwater（浙江水利厅，主）、nmc（中央气象台，备）、jma（日本气象厅，
  第二意见源）。前端只认统一 schema 不认源

## 接口形态确证状态

改任何解析代码前先看 CLAUDE.md 的已确证事实表，测试已把它们钉死。要点：

1. 风圈四段顺序 东北|东南|西北|西南（官方 SPA + 在产项目双重确证）
2. `TyhoonActivity` 时间为 UTC ISO，详情逐点为北京时间字符串，`norm_time` 两种都吃
3. nmc 数组偏移对照在产项目写死；jma 用命名字段较安全，强台风字段待在编时补验
4. 气压过物理区间 [850,1050]——个别机构把风力等级塞进气压字段（实测 HKO 9–14）
5. 复盘只算 lead>0 的真预报，排除 T+0 分析场（否则误差被系统性拉低）

细节与踩坑记录见 `docs/article-draft.md`。

## 部署

见 `docs/deploy.md`：托管两套方案对比、就绪清单、需拍板的花钱/域名/备案决策。
境外 GitHub Actions runner 已验证能连通 zjwater 并自动 push 数据。

## 合规红线

个人不得发布气象预报（《气象法》）。预测内容一律转发官方机构并署名；自研推演标
「情景模拟，不构成预报」；复盘为回溯校验（对已发生的事实），非预报。页脚免责声明不许删，
城市影响带「几何计算非预警」字样。

## 数据与素材来源

台风实况与预报：浙江省水利厅台风路径系统 / 中央气象台台风网 / 日本气象厅（非官方转发）。
陆地轮廓：Natural Earth（公有领域）。地图引擎：MapLibre GL JS（BSD-3）。
RMW 气候式：Willoughby et al. (2006)。
