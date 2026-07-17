/* splash.js — 雷达主题开屏动画（三页共用，零依赖）
 * 用法：<script src="splash.js" data-title="台风态势实验室" data-en="TYPHOON LAB"></script>
 * 页面就绪时调 window.dismissSplash() 收起；6s 兜底防加载异常卡屏。
 * 颜色取页面 CSS 变量（--ink/--accent/--text/--dim/--mono），随各页主题。 */
(function () {
  "use strict";
  var s = document.currentScript;
  var zh = (s && s.getAttribute("data-title")) || "台风态势实验室";
  var en = (s && s.getAttribute("data-en")) || "TYPHOON LAB";

  var css = "\
#splash{position:fixed;inset:0;z-index:50;background:var(--ink,#0a1420);\
  display:grid;place-items:center;transition:opacity .55s ease}\
#splash.gone{opacity:0;pointer-events:none}\
#splash .sp-wrap{display:flex;flex-direction:column;align-items:center;gap:22px}\
#splash .sp-radar{width:132px;height:132px;position:relative}\
#splash .sp-radar i{position:absolute;border:1px solid rgba(87,215,201,.18);border-radius:50%}\
#splash .sp-radar i.r1{inset:0}#splash .sp-radar i.r2{inset:22px}#splash .sp-radar i.r3{inset:44px}\
#splash .cross{position:absolute;inset:0}\
#splash .cross::before,#splash .cross::after{content:'';position:absolute;background:rgba(87,215,201,.14)}\
#splash .cross::before{left:50%;top:0;bottom:0;width:1px;transform:translateX(-.5px)}\
#splash .cross::after{top:50%;left:0;right:0;height:1px;transform:translateY(-.5px)}\
#splash .sp-sweep{position:absolute;inset:0;border-radius:50%;\
  background:conic-gradient(from 0deg,transparent 0deg,rgba(87,215,201,.32) 42deg,transparent 62deg);\
  animation:sp-spin 1.7s linear infinite}\
#splash .sp-pulse{position:absolute;inset:0;border-radius:50%;\
  border:1px solid rgba(87,215,201,.5);animation:sp-pulse 2.2s ease-out infinite}\
#splash .sp-eye{position:absolute;left:50%;top:50%;width:12px;height:12px;margin:-6px 0 0 -6px;\
  border-radius:50%;background:var(--accent,#57d7c9);box-shadow:0 0 14px rgba(87,215,201,.8)}\
#splash .sp-title{text-align:center;opacity:0;animation:sp-fade .9s ease .25s forwards}\
#splash .sp-title .zh{font-size:22px;font-weight:700;letter-spacing:.4em;padding-left:.4em;color:var(--text,#d7e3ee)}\
#splash .sp-title .en{margin-top:8px;font-family:var(--mono,monospace);font-size:10px;letter-spacing:.42em;color:var(--dim,#7b93a8)}\
@keyframes sp-spin{to{transform:rotate(360deg)}}\
@keyframes sp-pulse{0%{transform:scale(.42);opacity:.85}100%{transform:scale(1);opacity:0}}\
@keyframes sp-fade{to{opacity:1}}\
@media(prefers-reduced-motion:reduce){#splash .sp-sweep,#splash .sp-pulse{animation:none}#splash .sp-title{animation:none;opacity:1}}";

  var st = document.createElement("style");
  st.textContent = css;
  document.head.appendChild(st);

  var el = document.createElement("div");
  el.id = "splash";
  el.innerHTML =
    '<div class="sp-wrap"><div class="sp-radar">' +
    '<i class="r1"></i><i class="r2"></i><i class="r3"></i>' +
    '<span class="cross"></span><span class="sp-sweep"></span>' +
    '<span class="sp-pulse"></span><span class="sp-eye"></span></div>' +
    '<div class="sp-title"><div class="zh">' + zh + '</div>' +
    '<div class="en">' + en + '</div></div></div>';

  function mount() { if (document.body) document.body.appendChild(el); }
  if (document.body) mount();
  else document.addEventListener("DOMContentLoaded", mount);

  var t0 = Date.now();
  window.dismissSplash = function () {
    if (el.classList.contains("gone")) return;
    setTimeout(function () { el.classList.add("gone"); },
      Math.max(0, 650 - (Date.now() - t0)));
  };
  setTimeout(window.dismissSplash, 6000);   // 兜底
})();
