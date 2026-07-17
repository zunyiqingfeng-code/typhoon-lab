# 台风态势实验室（typhoon-lab）· M1

官方真实数据 + 可解释的参数化物理推演 + 教学级可视化。
本仓库是 M1：数据管道 + 地图底座。原则一条：**假数据永远明示**——
fixture 模式的页面右上角是红色「演示数据」角标，真实源才显示 LIVE。

## 目录

```
fetch_typhoon.py        数据管道，纯标准库单文件（zjwater 主源 / nmc 备源 / fixture）
data/                   管道输出：latest.json、index.json、archive/<年>/<编号>.json
web/index.html          前端单页（MapLibre，本地 vendor，无外网依赖）
web/vendor/             maplibre-gl v5.24.0（npm 官方发行文件原样落盘）
web/assets/land_wp.json Natural Earth 50m 陆地，裁剪至西太（90E–176E, 6S–56N）
scripts/build_standalone.py  打单文件预览（数据+地图库全内联，双击可开）
scripts/import_digital_typhoon.py  Digital Typhoon(JMA) GeoJSON 导入器，兼 M4 回填工具
tests/                  解析层测试与样本（python3 tests/test_parse.py）
.github/workflows/fetch.yml  每小时定时抓取，有变更才提交
```

## 快速开始

```bash
python3 fetch_typhoon.py --source fixture   # 离线：生成演示数据
python3 -m http.server 8000                 # 项目根目录起服务
# 浏览器打开 http://localhost:8000/web/

python3 fetch_typhoon.py                    # 真实抓取：zjwater 优先，失败切 nmc
python3 scripts/build_standalone.py         # 可选：打零依赖单文件预览
```

## 接口形态确证状态（2026-07-17）

以下均已核实，测试钉死在 `tests/test_parse.py`（18 项断言）：

1. **`/Api/TyhoonActivity` 已直连实测 200**（站方拼写就是少个 p），返回当前
   活跃快照；`time` 为 **UTC ISO** 格式（与详情接口的北京时间字符串并存，
   `norm_time()` 两种都吃）。
2. **风圈四段顺序已确证：`东北|东南|西北|西南`**（官方 SPA 前端与在产项目
   normalize.ts 双重对照），`norm_radius()` 按此映射。
3. `movedirection` 返回旧式方位命名（如 `西北西`=WNW），方位表已含两套命名。
4. 预报点 `speed/pressure` 为 `"0"` 表示缺测，不入库；`ybsj` 为预报发布时间。
5. nmc 备源数组偏移已按在产项目对照写死（`list_{year}` 行 `t[3]`=短编号、
   `view_{dbid}` 点位偏移见适配器 docstring）；仍属降级路径，首次启用建议抽查。
6. 唯一待本机确认项：从你的网络环境跑通 `python3 fetch_typhoon.py`（沙箱
   无法直连详情接口做全量抓取）。跑两次确认 `data/archive/` 合并去重不翻倍。

## 部署（长期运营向）

- 代码与数据仓库：GitHub。Actions 每小时抓取（境外 runner 若被源站封禁，
  见 workflow 尾注的两条退路）。
- 页面托管：境内可达优先——腾讯云 EdgeOne Pages 或 COS+CDN；
  GitHub Pages / Cloudflare Pages 在大陆可达性不稳，只当镜像。
- 绑自有域名走境内 CDN 需要 ICP 备案，这是「长期运营」路线上绕不开的一步，
  建议早备。
- 数据与页面同仓库静态分发即可，前端只读 JSON，无后端、无鉴权面。

## 合规红线

个人不得发布气象预报（《气象法》）。本项目的预测内容一律**转发官方机构**
并署名机构；自研推演（M3 起）一律标注「情景模拟，不构成预报」。
页脚免责声明不许删。

## 路线图与当前进度

- M2：预报锥（逐提前时刻 1.5σ 凸包）与城市影响估算（CPA 最近距离+时刻）
  **已在前端实现**，有多机构预报数据即自动可用；JMA 实时源待接（见 CLAUDE.md T3）。
- M3：**Holland (1980) 风场已实现**——B 由 Vmax/ΔP 反解、RMW 由 r7 二分反解
  （无 r7 用占位估计并明示）、移动不对称、20° 入流角、2D canvas 粒子平流、
  40 条集合扰动路径。WebGL 升级与引导气流积分见 CLAUDE.md T5。
- M4：Digital Typhoon 导入器与批量回填脚本已就绪
  （scripts/import_digital_typhoon.py / backfill_dt.py），
  历史选择器与相似路径检索见 CLAUDE.md T4。
- 交接：本地接力任务全在 **CLAUDE.md**（含验收标准与未验证项清单）。

## 数据与素材来源

台风实况与预报：浙江省水利厅台风路径系统 / 中央气象台台风网（非官方转发）。
陆地轮廓：Natural Earth（公有领域）。地图引擎：MapLibre GL JS（BSD-3）。
