// score_archive.mjs — 跨年度复盘评分预计算：node scripts/score_archive.mjs
// 读遍 data/archive/ 全部台风，用 eval.js 把每份历史预报对实况算误差，
// 按机构 × 提前量 × 年份聚合成小体积 data/scores.json，供 trends.html 整体加载。
// 浏览器加载不了 110MB 全归档，所以聚合放管道侧一次算好。
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";
import { createRequire } from "module";
const require = createRequire(import.meta.url);
const E = require("../web/eval.js");

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ARCHIVE = path.join(HERE, "..", "data", "archive");
const OUT = path.join(HERE, "..", "data", "scores.json");
const LEADS = [12, 24, 36, 48, 72, 96, 120];
const mean = a => (a && a.n ? +(a.sum / a.n).toFixed(1) : null);

const acc = {};            // 机构 → {pos,wind,pres,byLead}
const byYear = {};         // 年份 → 机构 → {sum,n}（位置误差）
let nStorms = 0, nPts = 0;

const years = fs.readdirSync(ARCHIVE).filter(y => /^\d{4}$/.test(y)).sort();
for (const y of years) {
  const dir = path.join(ARCHIVE, y);
  for (const f of fs.readdirSync(dir)) {
    if (!f.endsWith(".json")) continue;      // 跳过 dt_*.geojson 等
    const storm = JSON.parse(fs.readFileSync(path.join(dir, f), "utf8"));
    if (!storm.track || !storm.forecasts || !storm.forecasts.length) continue;
    nStorms++;
    for (const fc of storm.forecasts) {
      const ag = fc.agency;
      const a = acc[ag] || (acc[ag] =
        { pos: { sum: 0, n: 0 }, wind: { sum: 0, n: 0 },
          pres: { sum: 0, n: 0 }, byLead: {} });
      const ya = (byYear[y] || (byYear[y] = {}));
      const yag = ya[ag] || (ya[ag] = { sum: 0, n: 0, s72: 0, n72: 0 });
      for (const p of E.verifyIssuance(storm.track, fc).points) {
        nPts++;
        a.pos.sum += p.posErrKm; a.pos.n++;
        yag.sum += p.posErrKm; yag.n++;
        if (p.windErr != null) { a.wind.sum += p.windErr; a.wind.n++; }
        if (p.presErr != null) { a.pres.sum += p.presErr; a.pres.n++; }
        const b = E.nearestLead(p.lead, LEADS, 6);
        if (b != null) {
          const bl = a.byLead[b] || (a.byLead[b] = { sum: 0, n: 0 });
          bl.sum += p.posErrKm; bl.n++;
          if (b === 72) { yag.s72 += p.posErrKm; yag.n72++; }  // 逐年趋势固定看 72h
        }
      }
    }
  }
}

const agencies = Object.keys(acc).sort();
const overall = {};
for (const ag of agencies) {
  const a = acc[ag];
  overall[ag] = {
    n: a.pos.n, posMean: mean(a.pos),
    windMean: mean(a.wind), presMean: mean(a.pres),
    byLead: Object.fromEntries(LEADS.filter(l => a.byLead[l])
      .map(l => [l, { n: a.byLead[l].n, posMean: mean(a.byLead[l]) }])),
  };
}
const byYearOut = {};
for (const y of Object.keys(byYear).sort())
  byYearOut[y] = Object.fromEntries(Object.keys(byYear[y]).sort().map(ag => {
    const g = byYear[y][ag];
    return [ag, { n: g.n, posMean: mean(g),
      pos72Mean: g.n72 ? +(g.s72 / g.n72).toFixed(1) : null, n72: g.n72 }];
  }));

fs.writeFileSync(OUT, JSON.stringify({
  schema_version: "1.1",
  generated_at: new Date().toISOString(),
  n_storms: nStorms, n_points: nPts, years, leads: LEADS,
  agencies, overall, byYear: byYearOut,
}, null, 1) + "\n");
console.log(`评分完成：${nStorms} 台风、${nPts} 个可核验预报点、${agencies.length} 家机构 → ${path.relative(process.cwd(), OUT)}`);
