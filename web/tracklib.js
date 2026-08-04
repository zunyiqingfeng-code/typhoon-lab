/* tracklib.js — 共享前端库：强度色带 / 北京时 / 生命周期统计 / 双轴强度图
 * 供 index.html 与 archive.html 共用（两页需要同一套动画数据层，抽出避免重复）。
 * 纯函数 + DOM 辅助，不依赖 maplibre，可独立测试。 */
(function (root, factory) {
  var api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.TL = api;
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  /* 强度色带：国际通用（TD 蓝 → TS 绿 → 一级黄 → 三级橙 → 五级红）
     数据是 CMA 分级，按强度序映射同色序 */
  var GRADE = {
    TD: { c: "#4a9eff", zh: "热带低压" },
    TS: { c: "#35c46a", zh: "热带风暴" },
    STS: { c: "#f2c744", zh: "强热带风暴" },
    TY: { c: "#f2872e", zh: "台风" },
    STY: { c: "#f05252", zh: "强台风" },
    SuperTY: { c: "#dc2626", zh: "超强台风" },
  };
  var GRADE_ORDER = ["TD", "TS", "STS", "TY", "STY", "SuperTY"];
  var AGENCY = {
    CMA: { c: "#e6edf3", zh: "中央气象台" },
    JMA: { c: "#b48cf2", zh: "日本气象厅" },
    JTWC: { c: "#59b7ff", zh: "美国 JTWC" },
    HKO: { c: "#63e0b8", zh: "香港天文台" },
    CWA: { c: "#f2a0e0", zh: "台湾气象署" },
    KMA: { c: "#c9d86b", zh: "韩国气象厅" },
    SELF: { c: "#ff9d2e", zh: "SELF 自研" },
  };
  var R_EARTH = 6371.0;
  var D2R = Math.PI / 180;

  var p2 = function (n) { return String(n).padStart(2, "0"); };

  /* 北京时（数据本身即北京时口径），不随访客系统时区漂移 */
  function fmtT(iso) {
    var d = new Date(Date.parse(iso) + 8 * 3600e3);
    return p2(d.getUTCMonth() + 1) + "-" + p2(d.getUTCDate()) + " " +
      p2(d.getUTCHours()) + ":" + p2(d.getUTCMinutes());
  }
  function fmtFull(iso) {
    var d = new Date(Date.parse(iso) + 8 * 3600e3);
    return d.getUTCFullYear() + "-" + p2(d.getUTCMonth() + 1) + "-" +
      p2(d.getUTCDate()) + " " + p2(d.getUTCHours()) + ":" + p2(d.getUTCMinutes());
  }
  function bjHour(iso) { return new Date(Date.parse(iso) + 8 * 3600e3).getUTCHours(); }
  function grade(g) { return GRADE[g] || { c: "#8fa5b8", zh: "未知" }; }

  var DIR16 = ["北", "东北偏北", "东北", "东北偏东", "东", "东南偏东", "东南",
    "东南偏南", "南", "西南偏南", "西南", "西南偏西", "西", "西北偏西", "西北", "西北偏北"];
  function deg2zh(d) {
    return d == null ? "—" : DIR16[Math.round(((d % 360) + 360) % 360 / 22.5) % 16];
  }

  /* 大圆距离 km，输入 [lat,lon] */
  function haversineKm(a, b) {
    var f1 = a[0] * D2R, f2 = b[0] * D2R;
    var df = (b[0] - a[0]) * D2R, dl = (b[1] - a[1]) * D2R;
    var s = Math.sin(df / 2) * Math.sin(df / 2) +
      Math.cos(f1) * Math.cos(f2) * Math.sin(dl / 2) * Math.sin(dl / 2);
    return R_EARTH * 2 * Math.asin(Math.sqrt(s));
  }

  /* 路径总长 km（track 点数组，含 t/lat/lon） */
  function pathKm(track) {
    var sum = 0;
    for (var i = 1; i < track.length; i++)
      sum += haversineKm([track[i - 1].lat, track[i - 1].lon],
        [track[i].lat, track[i].lon]);
    return sum;
  }

  /* 生命周期统计 */
  function statsOf(track) {
    var maxW = null, minP = null, maxAt = null, minAt = null, mv = [];
    for (var i = 0; i < track.length; i++) {
      var p = track[i];
      if (p.wind_ms != null && (maxW == null || p.wind_ms > maxW)) { maxW = p.wind_ms; maxAt = p; }
      if (p.pressure_hpa != null && (minP == null || p.pressure_hpa < minP)) { minP = p.pressure_hpa; minAt = p; }
      if (p.move_speed_kmh != null) mv.push(p.move_speed_kmh);
    }
    var startMs = Date.parse(track[0].t), endMs = Date.parse(track[track.length - 1].t);
    return {
      startMs: startMs, endMs: endMs,
      durH: Math.round((endMs - startMs) / 3.6e6 * 10) / 10,
      pathKm: Math.round(pathKm(track)),
      maxWind: maxW, maxAt: maxAt,
      minPres: minP, minAt: minAt,
      meanSpeed: mv.length ? Math.round(mv.reduce(function (a, b) { return a + b; }, 0) / mv.length) : null,
      n: track.length,
    };
  }

  /* 抽稀：单台风点超 maxN 时，保留首末、强度极值点与等级跳变点，其余均匀取点 */
  function decimate(track, maxN) {
    var n = track.length;
    if (n <= maxN) return track;
    var keep = { 0: 1, [n - 1]: 1 };
    for (var i = 1; i < n - 1; i++) {
      var p = track[i];
      var isMax = p.wind_ms != null && (
        (track[i - 1].wind_ms == null || p.wind_ms >= track[i - 1].wind_ms) &&
        (track[i + 1].wind_ms == null || p.wind_ms > track[i + 1].wind_ms));
      if (isMax || p.grade !== track[i - 1].grade || p.grade !== track[i + 1].grade)
        keep[i] = 1;
    }
    var keys = function () { return Object.keys(keep).map(Number).sort(function (a, b) { return a - b; }); };
    if (Object.keys(keep).length > maxN) {       /* 极值点本身就超限 */
      var arr = keys();
      var stride = Math.ceil(arr.length / maxN);
      var out = [];
      for (var k = 0; k < arr.length; k += stride) out.push(track[arr[k]]);
      return out;
    }
    var stride2 = Math.ceil(n / (maxN - Object.keys(keep).length));
    for (var s = 0; s < n && Object.keys(keep).length < maxN; s += stride2) keep[s] = 1;
    return keys().map(function (i) { return track[i]; });
  }

  /* ---- 强度演变图（SVG 双轴：风速 m/s 左轴，气压 hPa 右轴反向，时间 x） ---- */
  /* opts: { onSeek(idx, xPx), cursor } — 悬停/点击回调与外部光标同步 */
  function intensityChart(host, track, opts) {
    opts = opts || {};
    var W = 620, H = 250, pad = { l: 44, r: 46, t: 14, b: 34 };
    var iw = W - pad.l - pad.r, ih = H - pad.t - pad.b;
    var t0 = Date.parse(track[0].t), t1 = Date.parse(track[track.length - 1].t);
    var span = Math.max(t1 - t0, 3600e3);
    var winds = track.map(function (p) { return p.wind_ms; }).filter(function (v) { return v != null; });
    var pres = track.map(function (p) { return p.pressure_hpa; }).filter(function (v) { return v != null; });
    var wMax = winds.length ? Math.max.apply(null, winds) : 60;
    var pMin = pres.length ? Math.min.apply(null, pres) : 940;
    var wTop = Math.ceil(wMax / 10) * 10, pBottom = Math.floor(pMin / 20) * 20;
    var pTop = 1010;
    var X = function (ms) { return pad.l + (ms - t0) / span * iw; };
    var Yw = function (w) { return pad.t + (1 - w / wTop) * ih; };
    var Yp = function (p) { return pad.t + (1 - (p - pBottom) / (pTop - pBottom)) * ih; };
    var esc = function (s) { return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;"); };

    var s = '<svg viewBox="0 0 ' + W + " " + H + '" role="img" aria-label="强度演变：风速与气压">';
    /* 强度色带底纹：每段按后点等级着色 */
    for (var i = 1; i < track.length; i++) {
      var x0 = X(Date.parse(track[i - 1].t)), x1 = X(Date.parse(track[i].t));
      s += '<rect x="' + x0.toFixed(1) + '" y="' + pad.t + '" width="' + (x1 - x0).toFixed(1) +
        '" height="' + ih + '" fill="' + grade(track[i].grade).c + '" opacity="0.05"/>';
    }
    /* 网格与轴 */
    var yticks = [];
    for (var w = 0; w <= wTop; w += 10) yticks.push(w);
    for (var g = 0; g < yticks.length; g++) {
      var yy = Yw(yticks[g]);
      s += '<line x1="' + pad.l + '" y1="' + yy.toFixed(1) + '" x2="' + (W - pad.r) +
        '" y2="' + yy.toFixed(1) + '" stroke="rgba(255,255,255,.07)"/>';
      s += '<text x="' + (pad.l - 8) + '" y="' + (yy + 3).toFixed(1) +
        '" fill="#7b93a8" font-size="10" text-anchor="end">' + yticks[g] + "</text>";
    }
    /* 右轴气压刻度 */
    var pticks = [];
    for (var p = 1000; p >= pBottom; p -= 20) pticks.push(p);
    for (var q = 0; q < pticks.length; q++) {
      var py = Yp(pticks[q]);
      s += '<text x="' + (W - pad.r + 8) + '" y="' + (py + 3).toFixed(1) +
        '" fill="#7b93a8" font-size="10">' + pticks[q] + "</text>";
    }
    /* x 轴时间刻度：每 24h 一根 */
    var firstD = new Date(t0 + 8 * 3600e3);
    var dayStart = Date.UTC(firstD.getUTCFullYear(), firstD.getUTCMonth(), firstD.getUTCDate()) - 8 * 3600e3;
    for (var tt = dayStart + 24 * 3600e3; tt < t1; tt += 24 * 3600e3) {
      var tx = X(tt);
      s += '<line x1="' + tx.toFixed(1) + '" y1="' + pad.t + '" x2="' + tx.toFixed(1) +
        '" y2="' + (pad.t + ih) + '" stroke="rgba(255,255,255,.05)"/>';
      s += '<text x="' + tx.toFixed(1) + '" y="' + (H - pad.b + 16) +
        '" fill="#7b93a8" font-size="10" text-anchor="middle">' +
        fmtT(new Date(tt).toISOString()).slice(0, 5) + "</text>";
    }
    s += '<text x="' + (pad.l - 40) + '" y="' + (pad.t + ih / 2) +
      '" fill="#7b93a8" font-size="10" transform="rotate(-90 ' + (pad.l - 40) + " " +
      (pad.t + ih / 2) + ')" text-anchor="middle">风速 m/s</text>';
    s += '<text x="' + (W - 8) + '" y="' + (pad.t + ih / 2) +
      '" fill="#7b93a8" font-size="10" transform="rotate(-90 ' + (W - 8) + " " +
      (pad.t + ih / 2) + ')" text-anchor="middle">气压 hPa</text>';
    /* 风速曲线 + 面积 */
    var wp = track.map(function (p, i) {
      return (i ? "L" : "M") + X(Date.parse(p.t)).toFixed(1) + " " +
        Yw(p.wind_ms == null ? 0 : Math.max(p.wind_ms, 0)).toFixed(1);
    }).join(" ");
    s += '<path d="' + wp + ' L' + X(t1).toFixed(1) + " " + (pad.t + ih).toFixed(1) +
      " L" + X(t0).toFixed(1) + " " + (pad.t + ih).toFixed(1) + ' Z" fill="url(#tgw)" opacity=".35"/>';
    s += '<path d="' + wp + '" fill="none" stroke="#22d3ee" stroke-width="2" stroke-linejoin="round"/>';
    /* 气压曲线 */
    if (pres.length) {
      var pp = track.map(function (p, i) {
        return (i ? "L" : "M") + X(Date.parse(p.t)).toFixed(1) + " " +
          (p.pressure_hpa == null ? Yw(0) : Yp(p.pressure_hpa)).toFixed(1);
      }).filter(function (seg, i) { return pres.length > 1 || i === 0; }).join(" ");
      s += '<path d="' + pp + '" fill="none" stroke="#f2c744" stroke-width="1.4" ' +
        'stroke-dasharray="4 3" opacity=".85"/>';
    }
    s += '<defs><linearGradient id="tgw" x1="0" y1="0" x2="0" y2="1">' +
      '<stop offset="0" stop-color="#22d3ee"/><stop offset="1" stop-color="#22d3ee" stop-opacity="0"/></linearGradient></defs>';
    /* 图例 */
    s += '<g font-size="10">';
    s += '<line x1="' + pad.l + '" y1="10" x2="' + (pad.l + 16) + '" y2="10" stroke="#22d3ee" stroke-width="2"/>';
    s += '<text x="' + (pad.l + 20) + '" y="13" fill="#d7e3ee">风速 m/s</text>';
    s += '<line x1="' + (pad.l + 100) + '" y1="10" x2="' + (pad.l + 116) + '" y2="10" stroke="#f2c744" stroke-dasharray="4 3"/>';
    s += '<text x="' + (pad.l + 120) + '" y="13" fill="#d7e3ee">气压 hPa（反向）</text>';
    s += "</g>";
    s += '<g id="tl-cursor">';
    s += '<line y1="' + pad.t + '" y2="' + (pad.t + ih) + '" stroke="#22d3ee" stroke-opacity=".8" stroke-width="1"/>';
    s += '<circle r="3.5" fill="#22d3ee"/>';
    s += "</g></svg>";
    host.innerHTML = s;

    var svg = host.querySelector("svg");
    var cursor = host.querySelector("#tl-cursor");
    var cx = null, cy = null;
    var readout = document.createElement("div");
    readout.className = "tl-readout";
    host.appendChild(readout);

    var nearest = function (xPx) {
      var wpx = svg.getBoundingClientRect().width;
      var scale = wpx / W;
      var mx = (xPx - pad.l * scale) / (iw * scale);
      var ms = t0 + span * Math.max(0, Math.min(1, mx));
      var best = 0, bd = Infinity;
      for (var i = 0; i < track.length; i++) {
        var d = Math.abs(Date.parse(track[i].t) - ms);
        if (d < bd) { bd = d; best = i; }
      }
      return best;
    };
    var move = function (xPx) {
      var i = nearest(xPx);
      var px = X(Date.parse(track[i].t));
      cx = px; cy = Yw(track[i].wind_ms == null ? 0 : track[i].wind_ms);
      cursor.setAttribute("transform", "translate(" + px.toFixed(1) + " 0)");
      cursor.querySelector("circle").setAttribute("cy", cy.toFixed(1));
      readout.innerHTML = esc(track[i].t ? fmtT(track[i].t) : "") +
        " · <b>" + esc(grade(track[i].grade).zh) + "</b> · " +
        (track[i].wind_ms != null ? track[i].wind_ms + " m/s" : "风速 —") + " · " +
        (track[i].pressure_hpa != null ? track[i].pressure_hpa + " hPa" : "气压 —");
      if (opts.onSeek) opts.onSeek(i, xPx);
    };
    host.addEventListener("mousemove", function (e) {
      var r = svg.getBoundingClientRect();
      move(e.clientX - r.left);
    });
    host.addEventListener("mouseleave", function () {
      cursor.style.display = "none"; readout.style.display = "none";
      if (opts.onLeave) opts.onLeave();
    });
    host.addEventListener("click", function (e) {
      var r = svg.getBoundingClientRect();
      if (opts.onPick) opts.onPick(nearest(e.clientX - r.left));
    });

    return {
      setCursor: function (i) {
        var px = X(Date.parse(track[i].t));
        cursor.setAttribute("transform", "translate(" + px.toFixed(1) + " 0)");
        cursor.querySelector("circle").setAttribute("cy",
          Yw(track[i].wind_ms == null ? 0 : track[i].wind_ms).toFixed(1));
        cursor.style.display = "";
        readout.style.display = "none";
        readout.innerHTML = "";
      },
      hideCursor: function () { cursor.style.display = "none"; },
    };
  }

  /* 形状相似度：对两条起点对齐、总长归一化的 32 点形状签名（build_shapes.py 产物，
   * 每组 [[latE5,lonE5]...]）逐点取大圆距离均值。平移/尺度不变（各减自身起点、除以
   * 自身总长），方向敏感（反向走的不算相似）。返回等效 km（在两条路径平均尺度上），
   * 0 为完全同形。预筛由调用方做（长度比/起点距离）。 */
  function shapeSimilarity(aPts, bPts, aLenKm, bLenKm) {
    var n = Math.min(aPts.length, bPts.length);
    if (n < 4) return null;
    var la = aLenKm || shapeLen(aPts), lb = bLenKm || shapeLen(bPts);
    var d = 0;
    for (var i = 0; i < n; i++) {
      var ax = (aPts[i][1] - aPts[0][1]) / la * 1e-5 * 111.32;
      var ay = (aPts[i][0] - aPts[0][0]) / la * 1e-5 * 110.57;
      var bx = (bPts[i][1] - bPts[0][1]) / lb * 1e-5 * 111.32;
      var by = (bPts[i][0] - bPts[0][0]) / lb * 1e-5 * 110.57;
      d += Math.hypot(ax - bx, ay - by);
    }
    d /= n;
    return { norm: d, eqKm: d * (la + lb) / 2,
      lenRatio: la / lb, lenKmA: la, lenKmB: lb };
  }
  function shapeLen(pts) {
    var s = 0;
    for (var i = 1; i < pts.length; i++) {
      s += haversineKm([pts[i - 1][0] / 1e5, pts[i - 1][1] / 1e5],
        [pts[i][0] / 1e5, pts[i][1] / 1e5]);
    }
    return s;
  }

  return {
    GRADE: GRADE, GRADE_ORDER: GRADE_ORDER, AGENCY: AGENCY,
    fmtT: fmtT, fmtFull: fmtFull, bjHour: bjHour, grade: grade, deg2zh: deg2zh,
    haversineKm: haversineKm, pathKm: pathKm, statsOf: statsOf,
    decimate: decimate, intensityChart: intensityChart,
    shapeSimilarity: shapeSimilarity,
  };
});
