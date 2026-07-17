// 复盘评测层测试：node tests/test_eval.mjs
import { createRequire } from "module";
const require = createRequire(import.meta.url);
const E = require("../web/eval.js");

let ok = 0;
function check(cond, msg) {
  if (!cond) { console.error("FAIL:", msg); process.exit(1); }
  ok++; console.log("  ok -", msg);
}

// 合成实况：一条 NW 直线，0..72h 每 12h 一个点
const base = Date.parse("2026-09-01T00:00:00Z");
const iso = h => new Date(base + h * 3600e3).toISOString();
const lat = h => 15 + 0.05 * h;
const lon = h => 135 - 0.06 * h;
const wind = h => 30 + 0.1 * h;
const pres = h => 990 - 0.2 * h;

const track = [0, 12, 24, 36, 48, 60, 72].map(h => ({
  t: iso(h), lat: lat(h), lon: lon(h), wind_ms: wind(h), pressure_hpa: pres(h),
}));

// 完美机构：发布于 0h，预报点正落在实况上；含一个 96h 点（超出实况跨度）
const perfect = { agency: "AAA", issued_at: iso(0), points: [24, 48, 72, 96].map(h => ({
  t: iso(h), lat: lat(h), lon: lon(h), wind_ms: wind(h), pressure_hpa: pres(h),
})) };
// 偏差机构：经度 +0.9°、风速 +5，发布于 0h
const biased = { agency: "BBB", issued_at: iso(0), points: [24, 48, 72].map(h => ({
  t: iso(h), lat: lat(h), lon: lon(h) + 0.9, wind_ms: wind(h) + 5,
  pressure_hpa: pres(h),
})) };
const storm = { id: "TEST", track, forecasts: [perfect, biased] };

// ---- 逐份核验 ----
const vp = E.verifyIssuance(track, perfect);
check(vp.points.length === 3, "超出实况跨度的 96h 预报点被丢弃（4→3）");
check(vp.points.every(p => p.posErrKm < 1), "完美预报位置误差≈0");
check(vp.points.every(p => p.windErr < 1e-6 && p.presErr < 1e-6),
      "完美预报强度误差≈0");
check(vp.points.map(p => p.lead).join(",") === "24,48,72", "提前量计算正确");

const vb = E.verifyIssuance(track, biased);
check(vb.points.every(p => p.posErrKm > 50 && p.posErrKm < 150),
      `偏差预报位置误差在合理量级（${vb.points[0].posErrKm.toFixed(0)} km）`);
check(Math.abs(E.mean(vb.points.map(p => p.windErr)) - 5) < 1e-6,
      "偏差预报风速误差≈5 m/s");

// ---- 越界不外推 ----
check(E.interpObserved(track, base - 3600e3) === null, "早于实况起点返回 null");
check(E.interpObserved(track, base + 200 * 3600e3) === null, "晚于实况终点返回 null");
const mid = E.interpObserved(track, base + 6 * 3600e3);
check(Math.abs(mid.lat - lat(6)) < 1e-9 && Math.abs(mid.lon - lon(6)) < 1e-9,
      "中间时刻线性插值正确");

// ---- 整场评分与排名 ----
const score = E.scoreStorm(storm);
check(score.agencies.join(",") === "AAA,BBB", "两机构都进评分表");
check(Object.keys(score.table.AAA.byBucket).sort((a, b) => a - b)
        .join(",") === "24,48,72", "按提前量分档");
const rank = E.agencyRanking(score);
check(rank[0].agency === "AAA" && rank[1].agency === "BBB",
      "总榜：完美机构位置误差更小排第一");
check(score.table.BBB.posMean > score.table.AAA.posMean,
      "偏差机构整体位置误差更大");

// ---- 时间轴辅助 ----
const by = E.issuancesByAgency(storm);
check(by.AAA.length === 1 && by.BBB.length === 1, "按机构归集发布时刻");
check(E.issuanceAt(storm, "AAA", iso(0)) === perfect, "按机构+发布时刻取回预报");

console.log(`\n复盘评测层全部通过：${ok} 项断言`);
