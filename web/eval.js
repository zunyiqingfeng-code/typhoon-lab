/* eval.js — 台风态势实验室 复盘评测层（纯函数，无 DOM 依赖）
 *
 * 做的是回溯审计，不是预报：把各机构历史上发布的预报点，与同一有效时刻
 * 的实况位置配对，算位置误差（大圆距离）与强度误差。全部基于已发生的事实。
 *
 * 口径：
 *  - 位置误差 = 预报点(lat,lon) 与「实况轨迹插值到该有效时刻」的大圆距离(km)
 *  - 强度误差 = |预报风速−实况风速|、|预报气压−实况气压|，任一缺测则跳过该项
 *  - 提前量 lead = 有效时刻 − 发布时刻（小时），只取 lead>=0
 *  - 只在实况时间跨度内核验；预报到了实况尚未覆盖的时刻（台风已停编）则丢弃该点
 *  - 按机构 × 提前量档（12/24/36/48/72/96/120h，容差 ±6h）聚合，出均值/中位数
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.TLEval = api;
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  const D2R = Math.PI / 180;

  function haversineKm(a, b) {
    const φ1 = a[0] * D2R, φ2 = b[0] * D2R;
    const dφ = (b[0] - a[0]) * D2R, dλ = (b[1] - a[1]) * D2R;
    const s = Math.sin(dφ / 2) ** 2 +
              Math.cos(φ1) * Math.cos(φ2) * Math.sin(dλ / 2) ** 2;
    return 6371 * 2 * Math.asin(Math.sqrt(s));
  }

  const lerp = (a, b, k) => a + (b - a) * k;
  const lerpMaybe = (a, b, k) =>
    (a == null || b == null) ? null : lerp(a, b, k);

  function mean(xs) {
    return xs.length ? xs.reduce((s, x) => s + x, 0) / xs.length : null;
  }
  function median(xs) {
    if (!xs.length) return null;
    const s = xs.slice().sort((a, b) => a - b), m = s.length >> 1;
    return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
  }

  /** 实况轨迹插值到绝对时刻 tMs；越出轨迹时间跨度返回 null（不外推） */
  function interpObserved(track, tMs) {
    if (!track || !track.length) return null;
    const t0 = Date.parse(track[0].t);
    const tN = Date.parse(track[track.length - 1].t);
    if (tMs < t0 || tMs > tN) return null;
    let prev = track[0];
    for (let i = 1; i < track.length; i++) {
      const cur = track[i], tc = Date.parse(cur.t), tp = Date.parse(prev.t);
      if (tc === tMs) return sample(cur);
      if (tc > tMs) {
        const k = tc === tp ? 0 : (tMs - tp) / (tc - tp);
        return {
          lat: lerp(prev.lat, cur.lat, k),
          lon: lerp(prev.lon, cur.lon, k),
          wind_ms: lerpMaybe(prev.wind_ms, cur.wind_ms, k),
          pressure_hpa: lerpMaybe(prev.pressure_hpa, cur.pressure_hpa, k),
        };
      }
      prev = cur;
    }
    return sample(track[track.length - 1]);
  }
  const sample = p => ({ lat: p.lat, lon: p.lon,
    wind_ms: p.wind_ms == null ? null : p.wind_ms,
    pressure_hpa: p.pressure_hpa == null ? null : p.pressure_hpa });

  const LEADS = [12, 24, 36, 48, 72, 96, 120];

  function nearestLead(h, buckets, tol) {
    let best = null, bd = Infinity;
    for (const b of buckets) {
      const d = Math.abs(h - b);
      if (d < bd) { bd = d; best = b; }
    }
    return bd <= tol ? best : null;
  }

  /** 核验单份预报（一个机构某发布时刻）：逐点配实况，出误差明细 */
  function verifyIssuance(track, fc) {
    const issMs = Date.parse(fc.issued_at);
    const pts = [];
    for (const p of fc.points || []) {
      const vMs = Date.parse(p.t);
      const lead = Math.round((vMs - issMs) / 3600e3);
      if (lead < 0) continue;
      const obs = interpObserved(track, vMs);
      if (!obs) continue;
      const windErr = (p.wind_ms != null && obs.wind_ms != null)
        ? Math.abs(p.wind_ms - obs.wind_ms) : null;
      const presErr = (p.pressure_hpa != null && obs.pressure_hpa != null)
        ? Math.abs(p.pressure_hpa - obs.pressure_hpa) : null;
      pts.push({
        lead, validMs: vMs,
        posErrKm: haversineKm([p.lat, p.lon], [obs.lat, obs.lon]),
        windErr, presErr,
        fc: [p.lat, p.lon], obs: [obs.lat, obs.lon],
      });
    }
    return { agency: fc.agency, issuedAt: fc.issued_at, points: pts };
  }

  /** 整场台风评分：按机构 × 提前量档聚合位置/强度误差 */
  function scoreStorm(storm, opts) {
    opts = opts || {};
    const buckets = opts.buckets || LEADS, tol = opts.tol || 6;
    const acc = {};
    for (const fc of storm.forecasts || []) {
      const v = verifyIssuance(storm.track, fc);
      const a = acc[fc.agency] ||
        (acc[fc.agency] = { pos: [], wind: [], pres: [], bk: {} });
      for (const p of v.points) {
        a.pos.push(p.posErrKm);
        if (p.windErr != null) a.wind.push(p.windErr);
        if (p.presErr != null) a.pres.push(p.presErr);
        const b = nearestLead(p.lead, buckets, tol);
        if (b != null) {
          const bb = a.bk[b] || (a.bk[b] = { pos: [], wind: [], pres: [] });
          bb.pos.push(p.posErrKm);
          if (p.windErr != null) bb.wind.push(p.windErr);
          if (p.presErr != null) bb.pres.push(p.presErr);
        }
      }
    }
    const agencies = Object.keys(acc).sort();
    const table = {};
    for (const ag of agencies) {
      const a = acc[ag], row = {
        n: a.pos.length, posMean: mean(a.pos), posMedian: median(a.pos),
        windMean: mean(a.wind), presMean: mean(a.pres), byBucket: {},
      };
      for (const b of buckets) {
        const bb = a.bk[b];
        if (bb && bb.pos.length) row.byBucket[b] = {
          n: bb.pos.length, posMean: mean(bb.pos), posMedian: median(bb.pos),
          windMean: mean(bb.wind), presMean: mean(bb.pres),
        };
      }
      table[ag] = row;
    }
    return { agencies, buckets, table };
  }

  /** 机构总榜：按整体位置误差均值升序（样本数须达门槛才计入排名） */
  function agencyRanking(score, minN) {
    minN = minN || 1;
    return score.agencies
      .map(ag => ({ agency: ag, n: score.table[ag].n,
                    posMean: score.table[ag].posMean }))
      .filter(r => r.n >= minN && r.posMean != null)
      .sort((a, b) => a.posMean - b.posMean);
  }

  /** 某机构的全部发布时刻（升序），供复盘时间轴用 */
  function issuancesByAgency(storm) {
    const by = {};
    for (const fc of storm.forecasts || [])
      (by[fc.agency] || (by[fc.agency] = [])).push(fc.issued_at);
    for (const ag in by) by[ag].sort();
    return by;
  }

  /** 取某机构某发布时刻那一份预报（找不到返回 null） */
  function issuanceAt(storm, agency, issuedAt) {
    for (const fc of storm.forecasts || [])
      if (fc.agency === agency && fc.issued_at === issuedAt) return fc;
    return null;
  }

  return { haversineKm, interpObserved, verifyIssuance, scoreStorm,
           agencyRanking, issuancesByAgency, issuanceAt, nearestLead,
           mean, median, LEADS };
});
