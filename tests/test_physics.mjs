// 物理层测试：node tests/test_physics.mjs
import { createRequire } from "module";
const require = createRequire(import.meta.url);
const P = require("../web/physics.js");

let ok = 0;
function check(cond, msg) {
  if (!cond) { console.error("FAIL:", msg); process.exit(1); }
  ok++; console.log("  ok -", msg);
}

// ---- Holland 剖面自洽 ----
const pt = { lat: 22, lon: 130, wind_ms: 45, pressure_hpa: 950,
             move_dir_deg: 315, move_speed_kmh: 20,
             r7: { ne: 300, se: 280, sw: 240, nw: 260 } };
const prm = P.estimateParams(pt);
check(prm.B >= 1 && prm.B <= 2.5, `B 在钳制区间（B=${prm.B.toFixed(2)}）`);
check(prm.rmwSource === "r7-inverse", "有 r7 时走反解路径");
const vAtRmw = P.gradientWind(prm.rmw, prm.rmw, prm.B, prm.dP, 5e-5);
check(Math.abs(vAtRmw - prm.vmax) / prm.vmax < 0.15,
      `RMW 处风速≈Vmax（${vAtRmw.toFixed(1)} vs ${prm.vmax}）`);
const r7m = (300 + 280 + 240 + 260) / 4;
check(Math.abs(P.gradientWind(r7m, prm.rmw, prm.B, prm.dP,
      2 * 7.2921e-5 * Math.sin(22 * Math.PI / 180)) - P.GALE) < 0.6,
      "r7 反解闭环：r7 处风速≈13.9 m/s");
check(P.gradientWind(600, prm.rmw, prm.B, prm.dP, 5e-5) <
      P.gradientWind(r7m, prm.rmw, prm.B, prm.dP, 5e-5), "外围风速单调衰减");

// ---- 风场矢量 ----
const st = P.makeState(pt);
const wN = P.windAt(st, pt.lat + prm.rmw / 111, pt.lon);   // 中心正北
check(wN && wN.u < 0, "北半球逆时针：中心正北处风向偏西（u<0）");
const wFar = P.windAt(st, pt.lat + 20, pt.lon);
check(wFar === null, "域外返回 null");

// ---- 集合与锥 ----
const cur = { t: "2026-07-17T08:00:00+08:00", lat: 20, lon: 132 };
const mkfc = (dlon) => ({ agency: "X", points: [24, 48, 72].map(h => ({
  t: new Date(Date.parse(cur.t) + h * 3600e3).toISOString(),
  lat: 20 + h / 24 * 1.5, lon: 132 - h / 24 * (1.2 + dlon) })) });
const fcs = [mkfc(0), mkfc(0.4), mkfc(-0.3)];
let seed = 42;
const rng = () => (seed = (seed * 1103515245 + 12345) % 2 ** 31) / 2 ** 31;
const ens = P.ensembleTracks(cur, fcs, 40, rng);
check(ens.length === 40 && ens.every(t => t.length >= 3), "集合 40 条成形");
const ends = ens.map(t => t[t.length - 1]);
const spread72 = Math.max(...ends.map(e =>
  P.haversineKm([e[0], e[1]], [ends[0][0], ends[0][1]])));
check(spread72 > 100, `72h+ 末端离散度合理展开（${Math.round(spread72)} km）`);
const cone = P.forecastCone(cur, fcs);
check(cone && cone.length >= 8 &&
      cone[0][0] === cone[cone.length - 1][0], "预报锥闭合多边形");
check(P.climoErrKm(48) > P.climoErrKm(24), "误差随提前量增长");

// ---- CPA ----
const impacts = P.cityImpacts(cur, fcs,
  [["近城", 22.9, 128.5], ["远城", 35, 140]], 250);
check(impacts[0].name === "近城" && impacts[0].distKm < impacts[1].distKm,
      "CPA 距离排序正确");
check(typeof impacts[0].t === "number", "CPA 时刻可用");

// ---- V&W(2008)：无 r7 时 RMW 气候回归 + B 经验式 ----
const noR7 = { lat: 22, lon: 130, wind_ms: 45, pressure_hpa: 950 };
const pf = P.estimateParams(noR7);
check(pf.rmwSource === "vw08", "无 r7 → V&W(2008) RMW 气候式");
const vwexp = Math.exp(3.015 - 6.291e-5 * 56 * 56 + 0.0337 * 22);
check(Math.abs(pf.rmw - vwexp) < 0.3,
      `RMW 命中 V&W 回归（${pf.rmw.toFixed(1)} km）`);
check(P.estimateParams({ lat: 22, lon: 130, wind_ms: 62, pressure_hpa: 915 }).rmw
      < pf.rmw, "同纬度更强台风 RMW 更小");
check(P.estimateParams({ lat: 35, lon: 130, wind_ms: 45, pressure_hpa: 950 }).rmw
      > pf.rmw, "同强度更高纬 RMW 更大");

// ---- V&W B 经验式逐项核对（式(10) + y=−2.237x+1.732） ----
const f22b = 2 * 7.2921e-5 * Math.sin(22 * Math.PI / 180);
const Aexp = 50000 * f22b /
             Math.sqrt(2 * 287 * 301.15 * Math.log(1 + 5600 / (95000 * Math.E)));
check(Math.abs(P.vickeryWadheraB(50, 22, 5600, 95000, 301.15) -
               (1.732 - 2.237 * Math.sqrt(Aexp))) < 1e-9, "V&W B 公式逐项命中");
check(Math.abs(P.vickeryRmw(56, 22) - vwexp) < 1e-9, "V&W RMW 回归逐项命中");

// ---- B 随 ΔP 变化（V&W 经验式：更深 → 剖面更陡、B 更大） ----
const bShallow = P.estimateParams({ lat: 20, lon: 130, wind_ms: 45, pressure_hpa: 975 }).B;
const bDeep = P.estimateParams({ lat: 20, lon: 130, wind_ms: 45, pressure_hpa: 945 }).B;
check(bDeep > bShallow && bShallow <= 2.5, "同 Vmax 下 ΔP 越大 B 越大（V&W）");

// ---- 剖面与风向补充 ----
const f22 = 2 * 7.2921e-5 * Math.sin(22 * Math.PI / 180);
check(P.gradientWind(prm.rmw * 0.5, prm.rmw, prm.B, prm.dP, f22) <
      P.gradientWind(prm.rmw, prm.rmw, prm.B, prm.dP, f22),
      "RMW 内侧风速小于 RMW 处");
const wE = P.windAt(st, pt.lat, pt.lon + prm.rmw / 111);   // 中心正东
check(wE && wE.v > 0, "北半球逆时针：中心正东处风向偏北（v>0）");
check(P.climoErrKm(24) > 45 && P.climoErrKm(24) < 95, "24h 气候平均误差量级合理");

console.log(`\n物理层全部通过：${ok} 项断言`);
