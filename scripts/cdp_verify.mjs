#!/usr/bin/env node
/* cdp_verify.mjs — 真实 Chrome CDP 渲染核验：四页 + 双 standalone。
   收集 console error、页面崩溃、关键 DOM 标记；index 额外检查 SELF 线/锥渲染。
   用法: node scripts/cdp_verify.mjs [--base file:///.../web/] [--self on] */
import { spawn, execSync } from "node:child_process";
import { createRequire } from "node:module";
const require = createRequire(import.meta.url);
import { readFileSync, rmSync, existsSync, statSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import http from "node:http";

const ROOT = path.dirname(fileURLToPath(import.meta.url)) + "/..";

function httpJson(url, method = "GET") {
  return new Promise((resolve, reject) => {
    const req = http.request(url, { method, agent: false }, res => {
      let b = "";
      res.on("data", c => b += c);
      res.on("end", () => {
        try { resolve(JSON.parse(b)); }
        catch (e) { reject(new Error("非JSON响应: " + b.slice(0, 60))); }
      });
    });
    req.on("error", reject);
    req.setTimeout(5000, () => { req.destroy(new Error("请求超时")); });
    req.end();
  });
}

async function json(url, method = "GET") {
  return httpJson(url, method);
}

const CHROME = "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe";
const PORT = 9333;
const UD = process.env.TEMP + "\\chrome-cdp-verify-ud";
const BASE = process.argv.includes("--base")
  ? process.argv[process.argv.indexOf("--base") + 1]
  : "file:///C:/Users/%E6%9B%BE%E9%87%91%E6%98%8C/Desktop/%E9%A1%B9%E7%9B%AE/%E5%8F%B0%E9%A3%8E%E7%BD%91%E9%A1%B5/typhoon-lab/typhoon-lab/web/";

const PAGES = ["home.html", "index.html", "verify.html", "trends.html", "archive.html"];
const STANDS = [
  "C:/Users/曾金昌/Desktop/项目/台风网页/typhoon-lab/typhoon-lab/台风态势_index_standalone.html",
  "C:/Users/曾金昌/Desktop/项目/台风网页/typhoon-lab/typhoon-lab/台风态势_archive_standalone.html",
];
const STAND_BASE = "file:///C:/Users/%E6%9B%BE%E9%87%91%E6%98%8C/Desktop/%E9%A1%B9%E7%9B%AE/%E5%8F%B0%E9%A3%8E%E7%BD%91%E9%A1%B5/typhoon-lab/typhoon-lab/";

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

async function cdp(ws, method, params = {}) {
  const id = ++cdp._id;
  return new Promise((resolve, reject) => {
    const t = setTimeout(() => reject(new Error("CDP 超时: " + method)), 30000);
    cdp._pend[id] = { resolve, reject, t };
    ws.send(JSON.stringify({ id, method, params }));
  });
}
cdp._id = 0; cdp._pend = {};

async function connect(wsUrl) {
  const ws = new WebSocket(wsUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  ws.addEventListener("message", ev => {
    const m = JSON.parse(ev.data);
    if (m.id && cdp._pend[m.id]) {
      const p = cdp._pend[m.id];
      clearTimeout(p.t);
      delete cdp._pend[m.id];
      m.error ? p.reject(new Error(m.error.message)) : p.resolve(m.result);
    }
  });
  return ws;
}

async function runPage(name, url, extra = {}) {
  const tab = await json(`http://127.0.0.1:${PORT}/json/new?${encodeURIComponent(url)}`, "PUT");
  const ws = await connect(tab.webSocketDebuggerUrl);
  const errors = [];
  await cdp(ws, "Runtime.enable");
  ws.addEventListener("message", ev => {
    const m = JSON.parse(ev.data);
    if (m.method === "Runtime.exceptionThrown") {
      const d = m.params.exceptionDetails || {};
      let loc = "";
      if (d.stackTrace && d.stackTrace.callFrames) {
        const f0 = d.stackTrace.callFrames[0];
        loc = ` @${(f0.url||"").split("/").pop()}:${f0.lineNumber}`;
      }
      errors.push((d.text || "exception") + loc +
        (d.exception && d.exception.description ? " | " + d.exception.description.slice(0,200) : ""));
    } else if (m.method === "Runtime.consoleAPICalled" && m.params.type === "error") {
      errors.push(m.params.args.map(a => a.value || a.description || "").join(" "));
    }
  });
  await sleep(extra.wait || 9000);           // 等地图/数据渲染
  let probe = null;
  if (extra.probe) {
    try {
      const r = await cdp(ws, "Runtime.evaluate", {
        expression: extra.probe, returnByValue: true, awaitPromise: true,
      });
      probe = r.result && r.result.value;
    } catch (e) { probe = "PROBE_FAIL " + e.message; }
  }
  await cdp(ws, "Target.closeTarget", { targetId: tab.id }).catch(() => {});
  ws.close();
  return { name, errors, probe };
}

let chrome = null;
async function killChromeTree() {
  if (chrome && chrome.pid) {
    try { execSync(`taskkill /F /T /PID ${chrome.pid}`, { stdio: "ignore" }); } catch {}
    chrome = null;
  }
}
async function main() {
  try { rmSync(UD, { recursive: true, force: true }); } catch {}
  chrome = spawn(CHROME, [
    "--headless=new", `--remote-debugging-port=${PORT}`,
    "--remote-allow-origins=*",
    "--disable-gpu", "--no-first-run", "--no-sandbox", "--user-data-dir=" + UD,
  ], { stdio: "ignore" });
  for (let i = 0; i < 20; i++) {          // 轮询等 DevTools 端点就绪
    try {
      const r = await json(`http://127.0.0.1:${PORT}/json/version`);
      if (r && r.webSocketDebuggerUrl) { break; }
    } catch { await sleep(500); }
  }
  try { await json(`http://127.0.0.1:${PORT}/json/version`); }
  catch (e) { console.error("Chrome 未启动:", e.message); process.exit(1); }

  const results = [];
  const fsrv = http.createServer((req, res) => {
    // 简单静态文件服务：web/ + data/
    const urlPath = decodeURIComponent((req.url || "/").split("?")[0]);
    const cands = [
      "web/" + urlPath,
      urlPath === "/" ? "web/index.html" : "",
      (urlPath.startsWith("/data/") ? urlPath.slice(1) : ""),
    ].filter(Boolean);
    for (const f of cands) {
      try {
        const full = path.join(ROOT, f);
        if (existsSync(full) && statSync(full).isFile()) {
          res.setHeader("Access-Control-Allow-Origin", "*");
          res.end(readFileSync(full));
          return;
        }
      } catch {}
    }
    res.statusCode = 404; res.end("nf");
  });
  const fsrvUrl = new Promise(res => fsrv.listen(8899, () => res()));
  await fsrvUrl;
  const httpBase = "http://127.0.0.1:8899/";
  for (const p of PAGES) {
    const r = await Promise.race([
      runPage(p, httpBase + p, { wait: p === "index.html" ? 12000 : 7000 }),
      sleep(40000).then(() => ({ name: p, errors: ["页面超时"], probe: null })),
    ]);
    results.push(r);
  }
  for (const [i, s] of STANDS.entries()) {
    const r = await runPage("standalone-" + (i + 1), "file:///" + s.replace(/\\/g, "/"),
      { wait: 8000 });
    results.push(r);
  }
  // index 页 SELF 专属检查（http 下最新数据）
  {
    const r = await runPage("index-self", httpBase + "index.html", {
      wait: 15000,
      probe: `(async()=>{
        try{
          const al = document.getElementById('agency-list');
          const rows = al ? [...al.querySelectorAll('.agency-row')] : [];
          const selfRow = rows.find(r=>(r.textContent||'').trim().startsWith('SELF'));
          if(selfRow){ const ck=selfRow.querySelector('input'); if(ck&&!ck.checked){ck.checked=true; ck.dispatchEvent(new Event('change'));} }
          const coneCk = document.getElementById('tg-cone');
          if(coneCk && !coneCk.checked){ coneCk.checked=true; coneCk.dispatchEvent(new Event('change')); }
          if(window.renderTime) window.renderTime();
          await new Promise(r=>setTimeout(r,1500));
          let coneFeat = -1;
          try{ const src = window.map && map.getSource('cone'); coneFeat = src && src._data ? (src._data.features||[]).length : -1; }catch(e){}
          let coneQry = -1;
          try{ if(window.map && map.loaded()){ const q = map.querySourceFeatures('cone'); coneQry = q.length; } }catch(e){}
          let coneLayer = null;
          try{ if(window.map){ const l = map.getLayer('cone-l'); coneLayer = l ? 'layer-exists' : 'no-layer'; } }catch(e){}
          let simState = null;
          try{ simState = { simCone: typeof sim!=='undefined' ? sim.cone : 'no-sim',
            tgConeDisabled: document.getElementById('tg-cone').disabled,
            selfFC: typeof storm!=='undefined' && storm() ? storm().forecasts.some(f=>f.agency==='SELF'&&f.cone) : 'no-storm',
            tIdx: typeof tIdx!=='undefined' ? tIdx : 'no-tIdx',
            trLen: typeof track!=='undefined' ? track().length : 'no-track',
            hasSimCone: typeof sim!=='undefined' && sim.state ? 'y' : 'n' }; }catch(e){ simState={err:e.message}; }
          return { maps: document.querySelectorAll('.maplibregl-canvas').length,
                   selfInAgencyList: !!selfRow, agencyCount: rows.length,
                   coneChecked: coneCk ? coneCk.checked : false,
                   coneFeat, coneQry, coneLayer, simState };
        }catch(e){ return {probeErr: e.message}; }
      })()`,
    });
    results.push(r);
  }
  fsrv.close();

  let fail = 0;
  for (const r of results) {
    const ok = r.errors.length === 0;
    if (!ok) fail++;
    console.log(`${ok ? "OK  " : "FAIL"} ${r.name}  console错误=${r.errors.length}`);
    for (const e of r.errors.slice(0, 5)) console.log("      " + e.slice(0, 160));
    if (r.probe) console.log("      probe:", JSON.stringify(r.probe));
  }
  console.log(fail ? `FAIL ${fail}/${results.length}` : `全部通过 ${results.length}/${results.length}`);
  await killChromeTree();
  process.exit(fail ? 1 : 0);
}

main().catch(async e => { console.error(e); await killChromeTree(); process.exit(1); });
