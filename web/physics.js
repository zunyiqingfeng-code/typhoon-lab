/* physics.js — 台风态势实验室 M3 物理层（纯函数，无 DOM 依赖）
 *
 * 一切输出都是「情景模拟 / 几何估算」，不是预报。UI 侧必须带此标注。
 *
 * 模型口径：
 *  - Holland (1980) 梯度风剖面：
 *      Vg(r) = sqrt( B·ΔP/ρ · (RMW/r)^B · e^{-(RMW/r)^B} + (rf/2)² ) − rf/2
 *  - B 用 Vickery–Wadhera (2008) 经验式（Vickery et al. 2009, J. Wind Eng.
 *    Ind. Aerodyn. 综述式(19) 与 Fig.10 回归 y = −2.237x + 1.732，R²=0.336）：
 *      B = 1.732 − 2.237·√A，A = RMW·f / √( 2·R_d·T_s·ln(1 + ΔP/(p_c·e)) )
 *    RMW 取 m，f = 2Ωsinφ，R_d = 287 J/(kg·K)，T_s 海温 K，ΔP/p_c 同单位
 *    —— 系数按 NOAA 综述核对；2007 waveworkshop 幻灯片 OCR 有 1.772 之歧，
 *       以综述为准。钳制 [1.0, 2.5]
 *  - RMW：有 7 级风圈时，与 B 固定点联立反解——令 Vg(r7)=13.9 m/s 对 RMW
 *    二分（r7 取四象限均值），再按上式更新 B，迭代至自洽；
 *    无 r7 时用 V&W(2008) 全样本气候回归（与 ParaTC 实现逐字核对）：
 *    ln(RMW) = 3.015 − 6.291e−5·Δp² + 0.0337·φ（Δp hPa、φ 纬度度、RMW km）
 *  - 非对称：叠加移动矢量，权重 w(r) = 2·RMW·r/(RMW²+r²)（RMW 处为 1）
 *  - 入流角 20° 指向中心；北半球气旋逆时针
 *  - 集合路径：对多机构预报的逐提前时刻均值做持续性随机游走扰动，
 *    σ(h) = max(机构间离散度, 气候平均路径误差 70·(h/24)^0.9 km)
 *  - 预报锥：各提前时刻以 1.5σ 为半径取圆，全部采样点做凸包
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.TLPhys = api;
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  const RHO = 1.15;              // 空气密度 kg/m³
  const OMEGA = 7.2921e-5;       // 地转角速度
  const PENV = 1006;             // 环境气压 hPa（西太夏季典型值）
  const GALE = 13.9;             // 7 级下限 m/s
  const D2R = Math.PI / 180;
  const RD = 287;                // 干空气气体常数 J/(kg·K)
  const TS_DEFAULT = 301.15;     // 默认海温 K（28°C，可被 pt.sst_c 覆盖）

  const clamp = (x, a, b) => Math.min(b, Math.max(a, x));

  /* ---- 球面小工具 ---- */
  function kmEastNorth(clat, clon, lat, lon) {
    return [(lon - clon) * 111.32 * Math.cos(((lat + clat) / 2) * D2R),
            (lat - clat) * 110.574];
  }
  function haversineKm(a, b) {
    const φ1 = a[0] * D2R, φ2 = b[0] * D2R;
    const dφ = (b[0] - a[0]) * D2R, dλ = (b[1] - a[1]) * D2R;
    const s = Math.sin(dφ / 2) ** 2 +
              Math.cos(φ1) * Math.cos(φ2) * Math.sin(dλ / 2) ** 2;
    return 6371 * 2 * Math.asin(Math.sqrt(s));
  }
  function destPoint(lat, lon, brgDeg, distKm) {
    const δ = distKm / 6371, θ = brgDeg * D2R;
    const φ1 = lat * D2R, λ1 = lon * D2R;
    const φ2 = Math.asin(Math.sin(φ1) * Math.cos(δ) +
                         Math.cos(φ1) * Math.sin(δ) * Math.cos(θ));
    const λ2 = λ1 + Math.atan2(Math.sin(θ) * Math.sin(δ) * Math.cos(φ1),
                               Math.cos(δ) - Math.sin(φ1) * Math.sin(φ2));
    return [φ2 / D2R, λ2 / D2R];         // [lat, lon]
  }

  /* ---- Holland 参数估计（B 用 V&W 2008 经验式） ---- */
  function vickeryWadheraB(rmwKm, latDeg, dPPa, pcPa, tsK) {
    const A = (rmwKm * 1000 * coriolis(latDeg)) /
              Math.sqrt(2 * RD * tsK * Math.log(1 + dPPa / (pcPa * Math.E)));
    return clamp(1.732 - 2.237 * Math.sqrt(A), 1.0, 2.5);
  }

  function vickeryRmw(dPmb, latDeg) {
    return clamp(Math.exp(3.015 - 6.291e-5 * dPmb * dPmb +
                          0.0337 * Math.abs(latDeg)), 10, 100);
  }

  function estimateParams(pt) {
    const vmax = pt.wind_ms || 18;
    const pc = pt.pressure_hpa || (PENV - 8);
    const dP = Math.max(3, PENV - pc) * 100;              // Pa
    const tsK = 273.15 + (pt.sst_c == null ? 28 : pt.sst_c);
    const lat = Math.abs(pt.lat == null ? 20 : pt.lat);
    const f = coriolis(lat);
    const bHolland = clamp(RHO * Math.E * vmax * vmax / dP, 1.0, 2.5);
    let B = bHolland, rmw = null, rmwSource = "fallback";
    const r7 = pt.r7 ? (pt.r7.ne + pt.r7.se + pt.r7.sw + pt.r7.nw) / 4 : null;
    if (r7 && r7 > 20 && vmax > GALE + 1) {
      // Vg(r7)=GALE 对 RMW 单调递增 → 二分；与 V&W B 固定点联立
      const bisect = b => {
        let lo = 8, hi = Math.min(120, r7 * 0.9);
        for (let i = 0; i < 40; i++) {
          const mid = (lo + hi) / 2;
          (gradientWind(r7, mid, b, dP, f) < GALE ? lo = mid : hi = mid);
        }
        return (lo + hi) / 2;
      };
      for (let it = 0; it < 6; it++) {
        rmw = bisect(B);
        B = vickeryWadheraB(rmw, lat, dP, pc * 100, tsK);
      }
      rmw = bisect(B);                                    // 末轮用最终 B 精化
      rmwSource = "r7-inverse";
    } else {
      // Vickery & Wadhera (2008) 全样本 RMW 回归（Δp hPa、纬度度 → RMW km）
      rmw = vickeryRmw(dP / 100, lat);
      B = vickeryWadheraB(rmw, lat, dP, pc * 100, tsK);
      rmwSource = "vw08";
    }
    return { vmax, pc, dP, B, bHolland, rmw, rmwSource, tsK };
  }

  const coriolis = lat => Math.abs(2 * OMEGA * Math.sin(lat * D2R));

  function gradientWind(rKm, rmwKm, B, dP, f) {
    const r = Math.max(rKm, 0.5) * 1000, rmw = rmwKm * 1000;
    const x = Math.pow(rmw / r, B);
    const core = (B * dP / RHO) * x * Math.exp(-x);
    return Math.sqrt(core + (r * f / 2) ** 2) - r * f / 2;
  }

  /* 移动矢量：由 move_dir/speed，缺省时由前后点推算（调用方保证） */
  function motionUV(pt) {
    if (pt.move_dir_deg == null || pt.move_speed_kmh == null) return [0, 0];
    const v = pt.move_speed_kmh / 3.6;                    // m/s
    const θ = pt.move_dir_deg * D2R;                      // 方位角，去向
    return [v * Math.sin(θ), v * Math.cos(θ)];            // [东, 北]
  }

  /** 风场采样：返回 {u,v,spd}（m/s，东/北分量）。state 由 makeState 生成 */
  function windAt(state, lat, lon) {
    const [dx, dy] = kmEastNorth(state.lat, state.lon, lat, lon);
    const r = Math.hypot(dx, dy);
    if (r > state.domainKm) return null;
    const vg = gradientWind(r, state.rmw, state.B, state.dP, state.f);
    // 半径方向单位矢（指外）；切向 = 逆时针 = 径向左转90°：(-dy,dx)/r
    const inv = 1 / Math.max(r, 0.5);
    const rx = dx * inv, ry = dy * inv;
    const beta = 20 * D2R;                                // 入流角
    const cb = Math.cos(beta), sb = Math.sin(beta);
    // 切向再向内旋 beta：t' = cosβ·t − sinβ·r̂
    let u = vg * (-ry * cb - rx * sb);
    let v = vg * (rx * cb - ry * sb);
    const w = 2 * state.rmw * r / (state.rmw * state.rmw + r * r);
    u += w * state.mu; v += w * state.mv;
    return { u, v, spd: Math.hypot(u, v) };
  }

  function makeState(pt) {
    const p = estimateParams(pt);
    const [mu, mv] = motionUV(pt);
    return {
      lat: pt.lat, lon: pt.lon, f: coriolis(pt.lat),
      B: p.B, dP: p.dP, rmw: p.rmw, vmax: p.vmax,
      rmwSource: p.rmwSource, mu, mv,
      domainKm: Math.max(600, (pt.r7 ? Math.max(pt.r7.ne, pt.r7.nw,
        pt.r7.se, pt.r7.sw) : 300) * 2.2),
    };
  }

  /* ---- 预报几何：机构轨迹按提前小时插值 ---- */
  function interpTrack(points, baseT, h) {
    const target = baseT + h * 3600e3;
    let prev = null;
    for (const q of points) {
      const t = Date.parse(q.t);
      if (t === target) return [q.lat, q.lon];
      if (t > target) {
        if (!prev) return null;
        const t0 = Date.parse(prev.t), k = (target - t0) / (t - t0);
        return [prev.lat + (q.lat - prev.lat) * k,
                prev.lon + (q.lon - prev.lon) * k];
      }
      prev = q;
    }
    return null;
  }

  const climoErrKm = h => 70 * Math.pow(h / 24, 0.9);

  /** 逐提前时刻共识：均值位置 + σ */
  function consensus(cur, forecasts, leads) {
    const baseT = Date.parse(cur.t);
    return leads.map(h => {
      const pts = [];
      for (const f of forecasts) {
        const p = interpTrack(f.points, baseT, h);
        if (p) pts.push(p);
      }
      if (!pts.length) return null;
      const mlat = pts.reduce((s, p) => s + p[0], 0) / pts.length;
      const mlon = pts.reduce((s, p) => s + p[1], 0) / pts.length;
      let spread = 0;
      for (const p of pts)
        spread = Math.max(spread, haversineKm(p, [mlat, mlon]));
      return { h, lat: mlat, lon: mlon,
               sigma: Math.max(spread, climoErrKm(h)) };
    }).filter(Boolean);
  }

  /* 高斯随机（Box–Muller），可注入 rng 便于测试 */
  function gauss(rng) {
    const u = Math.max(rng(), 1e-9), v = rng();
    return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
  }

  /** 集合路径：n 条扰动轨迹，每条 = [[lat,lon],...]（首点为当前位置） */
  function ensembleTracks(cur, forecasts, n, rng) {
    rng = rng || Math.random;
    const leads = [6, 12, 18, 24, 36, 48, 60, 72, 96, 120];
    const cons = consensus(cur, forecasts, leads);
    if (cons.length < 2) return [];
    const out = [];
    for (let m = 0; m < n; m++) {
      let ax = gauss(rng), ay = gauss(rng);
      const trk = [[cur.lat, cur.lon]];
      for (const c of cons) {
        ax = 0.9 * ax + 0.435 * gauss(rng);   // AR(1)，方差≈1
        ay = 0.9 * ay + 0.435 * gauss(rng);
        const east = ax * c.sigma, north = ay * c.sigma;
        const p1 = destPoint(c.lat, c.lon, 90, east);
        const p2 = destPoint(p1[0], p1[1], 0, north);
        trk.push(p2);
      }
      out.push(trk);
    }
    return out;
  }

  /* Andrew 单调链凸包，输入 [[lon,lat],...] */
  function convexHull(pts) {
    const p = pts.slice().sort((a, b) => a[0] - b[0] || a[1] - b[1]);
    if (p.length < 3) return p;
    const cross = (o, a, b) =>
      (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);
    const lo = [], hi = [];
    for (const q of p) {
      while (lo.length >= 2 && cross(lo[lo.length - 2], lo[lo.length - 1], q) <= 0)
        lo.pop();
      lo.push(q);
    }
    for (const q of p.reverse()) {
      while (hi.length >= 2 && cross(hi[hi.length - 2], hi[hi.length - 1], q) <= 0)
        hi.pop();
      hi.push(q);
    }
    lo.pop(); hi.pop();
    return lo.concat(hi);
  }

  /** 预报锥多边形环 [[lon,lat],...]（闭合） */
  function forecastCone(cur, forecasts) {
    const leads = [6, 12, 24, 36, 48, 72, 96, 120];
    const cons = consensus(cur, forecasts, leads);
    if (cons.length < 2) return null;
    const samples = [[cur.lon, cur.lat]];
    for (const c of cons) {
      const rad = 1.5 * c.sigma;
      for (let a = 0; a < 360; a += 20) {
        const p = destPoint(c.lat, c.lon, a, rad);
        samples.push([p[1], p[0]]);
      }
    }
    const hull = convexHull(samples);
    if (hull.length >= 3) hull.push(hull[0]);
    return hull.length >= 4 ? hull : null;
  }

  /** 城市 CPA：沿（当前点+共识路径）30 分钟步插值，求最近距离与时刻 */
  function cityImpacts(cur, forecasts, cities, r7MeanKm) {
    const leads = [6, 12, 18, 24, 36, 48, 60, 72, 96, 120];
    const cons = consensus(cur, forecasts, leads);
    if (!cons.length) return [];
    const baseT = Date.parse(cur.t);
    const path = [{ t: baseT, lat: cur.lat, lon: cur.lon }];
    for (const c of cons)
      path.push({ t: baseT + c.h * 3600e3, lat: c.lat, lon: c.lon });
    // 30 分钟细分
    const fine = [];
    for (let i = 1; i < path.length; i++) {
      const a = path[i - 1], b = path[i];
      const steps = Math.max(1, Math.round((b.t - a.t) / 1.8e6));
      for (let s = 0; s < steps; s++) {
        const k = s / steps;
        fine.push({ t: a.t + (b.t - a.t) * k,
                    lat: a.lat + (b.lat - a.lat) * k,
                    lon: a.lon + (b.lon - a.lon) * k });
      }
    }
    fine.push(path[path.length - 1]);
    return cities.map(([name, clat, clon]) => {
      let best = null;
      for (const q of fine) {
        const d = haversineKm([clat, clon], [q.lat, q.lon]);
        if (!best || d < best.d) best = { d, t: q.t };
      }
      return { name, distKm: Math.round(best.d), t: best.t,
               inGale: r7MeanKm != null && best.d <= r7MeanKm };
    }).sort((a, b) => a.distKm - b.distKm);
  }

  return { estimateParams, vickeryWadheraB, vickeryRmw,
           gradientWind, windAt, makeState,
           ensembleTracks, forecastCone, cityImpacts, consensus,
           haversineKm, destPoint, convexHull, climoErrKm, GALE };
});
