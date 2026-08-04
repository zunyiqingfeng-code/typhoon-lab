#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fetch_sources.py — 台风多源适配器（KMA / HKO / PAGASA / CWA / JTWC-UCAR）

探源结论（2026-08-03 本机直连实测）：
  KMA     https://www.kma.go.kr/neng/typhoon/typhoon-information.do
          英文版 HTML 表：分析 1 行 + 预报 7 行（12h 步进到 120h），
          列含时间/强度码/风速/气压/位置/移向/移速/风圈。直连 OK。
  HKO     https://www.weather.gov.hk/hko/textonly/tc/tcp.htm
          文本页：Forecast Positions + Past Positions 纯文本表。
          官方 JSON API（dataType=TCT/TCR）参数未知，弃用。
  PAGASA  https://www.pagasa.dost.gov.ph/tropical-cyclone/severe-weather-bulletin
          公报列表页含 TCB#N_<名>.pdf 链接；PDF 用 pypdf 提取文本解析。
          JSON API（/api/CycloneTrack 等）已 404，不可用。
  CWA     https://www.cwa.gov.tw/V8/E/P/Typhoon/TY_WARN.html
          页面 JS 渲染，数据在 /Data/js/typhoon/TY_WARN-Data.js
          （HTML 片段，含中心位置/气压/风速/暴风半径/预报点），
          仅台风警报期间有数据，无警报时为空。
  JTWC    https://hurricanes.ral.ucar.edu/repository/data/bdecks_open/<年>/
          UCAR 镜像 b-deck（bwp*.dat），BEST 行=JTWC 实况 6h 步进轨迹，
          可补 JTWC 独立实况（预报仍走 nmc 转发）。

依赖：PAGASA 需 pypdf（纯标准库其他源不依赖）。
"""

import re
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone

TZ_BJ = timezone(timedelta(hours=8))
TZ_UTC = timezone.utc
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

GRADE_KW = {
    "tropical depression": "TD", "low pressure area": "TD",
    "tropical storm": "TS", "severe tropical storm": "STS",
    "typhoon": "TY", "severe typhoon": "STY", "super typhoon": "SuperTY",
}

KMA_INTENSITY = {"1": "TD", "2": "TS", "3": "STS", "4": "TY", "5": "STY"}


def http_get(url, referer=None, timeout=20, retries=3):
    headers = {"User-Agent": UA}
    if referer:
        headers["Referer"] = referer
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                try:
                    return raw.decode("utf-8")
                except UnicodeDecodeError:
                    return raw.decode("gbk", errors="replace")
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(1.5 * (i + 1))
    raise last


def to_num(v):
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        try:
            return float(str(v).strip())
        except (TypeError, ValueError):
            return None


def norm_pressure(v):
    n = to_num(v)
    return n if n is not None and 850 <= n <= 1050 else None


def norm_time(v):
    if not v:
        return None
    s = str(v).strip()
    if "T" in s:
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=TZ_BJ)
            return dt.astimezone(TZ_BJ).isoformat()
        except ValueError:
            return None
    s = s.replace("/", "-")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d %H"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=TZ_BJ).isoformat()
        except ValueError:
            continue
    return None


def parse_hkt_time(s):
    """'Tue, 4 Aug 2026, 00:00' → ISO +08:00（容忍 Analysis/Forecast 后缀）"""
    s = re.sub(r"\s+(Analysis|Forecast)$", "", s.strip(), flags=re.I)
    try:
        dt = datetime.strptime(s, "%a, %d %b %Y, %H:%M")
        return dt.replace(tzinfo=TZ_UTC).astimezone(TZ_BJ).isoformat()
    except ValueError:
        return None


def make_point(t, lat, lon, **kw):
    p = {"t": t, "lat": lat, "lon": lon}
    p.update({k: v for k, v in kw.items() if v is not None})
    return p


def grade_from_wind(w):
    if w is None:
        return None
    if w >= 51:
        return "SuperTY"
    if w >= 41.5:
        return "STY"
    if w >= 32.7:
        return "TY"
    if w >= 24.5:
        return "STS"
    if w >= 17.2:
        return "TS"
    return "TD"


def grade_from_kw(s):
    if not s:
        return None
    return GRADE_KW.get(str(s).lower(), None)


# ---------------------------------------------------------------- KMA

class KmaAdapter:
    name = "kma"
    URL = "https://www.kma.go.kr/neng/typhoon/typhoon-information.do"

    def fetch_active(self):
        """返回 [{storm-like}]。无活跃台风返回空列表（页面仍 200）。"""
        txt = http_get(self.URL)
        storms = []
        for m in re.finditer(r'<div class="title"><strong>([^<]+)</strong>([^<]*)</div>', txt):
            header = m.group(0)
            block = txt[m.end():]
            block_end = block.find('</div>', block.find('</table>'))
            table_end = block.find('</table>')
            if table_end < 0:
                continue
            block = block[:table_end]
            storms.append(self._parse_block(header, block))
        if not storms:
            # 兜底：按 tab 名找台风名，抓第一块表
            m = re.search(r'<span>No\.(\d+) ([A-Z]+)</span>', txt)
            if m:
                storms = [self._parse_block(m.group(0),
                           txt[txt.find('<tbody'):txt.find('</table>')])]
        return [s for s in storms if s]

    def _parse_block(self, header, block):
        name_m = re.search(r'No\.(\d+)\s*([A-Z]+)', header)
        if not name_m:
            return None
        num, name_en = name_m.group(1), name_m.group(2)
        rows = re.findall(r'<tr>(.*?)</tr>', block, re.S)
        track, fpts, issued = [], [], None
        for row in rows:
            tds = re.findall(r'<td>(.*?)</td>', row, re.S)
            if len(tds) < 9:
                continue
            cells = [re.sub(r'<[^>]+>', ' ', c) for c in tds]
            cells = [re.sub(r'\s+', ' ', c).strip() for c in cells]
            # 0=时间 1=强度码 2=风速 3=风速km/h 4=气压 5=纬度 6=经度 7=移向 8=移速 9..=风圈
            tm = parse_hkt_time(cells[0])
            if not tm:
                continue
            lat, lon = to_num(cells[5]), to_num(cells[6])
            if lat is None or lon is None:
                continue
            w = to_num(cells[2])
            press = norm_pressure(cells[4])
            grade = KMA_INTENSITY.get(cells[1].strip()) or grade_from_wind(w)
            # 风圈 15/25 m/s：'450 [SW 350]' → 四象限
            r15 = self._parse_radius(cells[9]) if len(cells) > 9 else None
            r25 = self._parse_radius(cells[10]) if len(cells) > 10 else None
            p = make_point(tm, lat, lon,
                           pressure_hpa=press, wind_ms=w, grade=grade,
                           move_dir_deg=self._dir(cells[7]),
                           move_speed_kmh=to_num(cells[8]),
                           r7=r15, r10=r25)
            if "analysis" in cells[0].lower():
                track.append(p)
            else:
                fpts.append(p)
        if not track and not fpts:
            return None
        sid = "%d%02d" % (datetime.now(TZ_BJ).year, int(num))
        st = {"id": sid, "name_zh": "", "name_en": name_en,
              "is_active": True, "basin": "WP",
              "track": sorted(track, key=lambda p: p["t"])}
        if fpts:
            st["forecasts"] = [{"agency": "KMA",
                                "issued_at": (track[-1]["t"] if track else fpts[0]["t"]),
                                "points": sorted(fpts, key=lambda p: p["t"])}]
        else:
            st["forecasts"] = []
        return st

    @staticmethod
    def _parse_radius(cell):
        m = re.match(r'(\d+)', cell)
        if not m:
            return None
        base = int(m.group(1))
        r = {"ne": base, "se": base, "sw": base, "nw": base}
        qm = re.search(r'\[(N|S|E|W|NE|NW|SE|SW)\s+(\d+)\]', cell)
        if qm:
            r[qm.group(1).lower()] = int(qm.group(2))
        return r

    @staticmethod
    def _dir(s):
        d = {"N": 0, "NNE": 22.5, "NE": 45, "ENE": 67.5, "E": 90, "ESE": 112.5,
             "SE": 135, "SSE": 157.5, "S": 180, "SSW": 202.5, "SW": 225,
             "WSW": 247.5, "W": 270, "WNW": 292.5, "NW": 315, "NNW": 337.5}
        return d.get(str(s).strip().upper())


# ---------------------------------------------------------------- HKO

class HkoAdapter:
    name = "hko"
    URL = "https://www.weather.gov.hk/hko/textonly/tc/tcp.htm"

    def fetch_active(self):
        txt = http_get(self.URL)
        txt = re.sub(r"<[^>]+>", " ", txt)
        txt = re.sub(r"\s+", " ", txt)
        storms = []
        # 当前描述行：'Low Pressure Area at 11:00 HKT 03 August 2026 ( 17.1 N, 123.0 E ...)'
        pos_m = re.search(r"at ([\d:.]+ HKT [\d]+ [A-Za-z]+ \d{4})\s*\(\s*([\d.]+) ([NS])[, ]?\s*([\d.]+) ([EW])", txt)
        if not pos_m:
            return []  # 页面无台风信息
        tm = self._parse_hkt(pos_m.group(1))
        lat = to_num(pos_m.group(2))
        lon = to_num(pos_m.group(4))
        if pos_m.group(3) == "S":
            lat = -lat
        if pos_m.group(5) == "W":
            lon = -lon
        if tm is None or lat is None or lon is None:
            return []
        # Past Positions 块（HKO 风速单位 km/h → m/s）
        past = self._slice(txt, "Past Positions and Intensities", "Notes:")
        track = []
        for m in re.finditer(
                r"([\d:.]+ HKT [\d]+ [A-Za-z]+ \d{4})\s+([\d.]+) ([NS])\s+([\d.]+) ([EW])\s+([A-Za-z ]+?)\s+(\d+)\s*km/h", past):
            t = self._parse_hkt(m.group(1))
            la, lo = to_num(m.group(2)), to_num(m.group(4))
            if m.group(3) == "S":
                la = -la
            if m.group(5) == "W":
                lo = -lo
            w = to_num(m.group(7))
            w = w / 3.6 if w is not None else None
            if t is None or la is None or lo is None:
                continue
            track.append(make_point(t, la, lo, wind_ms=w,
                                    grade=grade_from_wind(w),
                                    pressure_hpa=None))
        # Forecast Positions 块
        fc = self._slice(txt, "Forecast Positions and Intensities",
                         "Past Positions and Intensities")
        fpts = []
        for m in re.finditer(
                r"([\d:.]+ HKT [\d]+ [A-Za-z]+ \d{4})\s+([\d.]+) ([NS])\s+([\d.]+) ([EW])\s+([A-Za-z ]+?)\s+(\d+)\s*km/h", fc):
            t = self._parse_hkt(m.group(1))
            la, lo = to_num(m.group(2)), to_num(m.group(4))
            if m.group(3) == "S":
                la = -la
            if m.group(5) == "W":
                lo = -lo
            w = to_num(m.group(7))
            w = w / 3.6 if w is not None else None
            if t is None or la is None or lo is None:
                continue
            fpts.append(make_point(t, la, lo, wind_ms=w,
                                   grade=grade_from_kw(m.group(6))))
        if not track and not fpts:
            return []
        st = {"id": "HKO-ACT", "name_zh": "", "name_en": "ACTIVE",
              "is_active": True, "basin": "WP",
              "track": sorted(track, key=lambda p: p["t"])}
        if fpts:
            st["forecasts"] = [{"agency": "HKO",
                                "issued_at": (track[-1]["t"] if track else fpts[0]["t"]),
                                "points": sorted(fpts, key=lambda p: p["t"])}]
        else:
            st["forecasts"] = []
        return [st]

    @staticmethod
    def _slice(txt, start, end):
        i = txt.find(start)
        j = txt.find(end, i + 1) if i >= 0 else -1
        if i < 0:
            return ""
        return txt[i:j if j > i else len(txt)]

    @staticmethod
    def _parse_hkt(s):
        """'11:00 HKT 03 August 2026' → ISO +08:00"""
        try:
            dt = datetime.strptime(s.strip(), "%H:%M HKT %d %B %Y")
            return dt.replace(tzinfo=TZ_BJ).isoformat()
        except ValueError:
            return None


# ---------------------------------------------------------------- PAGASA

class PagasaAdapter:
    name = "pagasa"
    LIST_URL = "https://www.pagasa.dost.gov.ph/tropical-cyclone/severe-weather-bulletin"

    def fetch_active(self):
        try:
            from pypdf import PdfReader
        except ImportError:
            raise RuntimeError("PAGASA 需 pypdf（pip install pypdf）")
        txt = http_get(self.LIST_URL)
        links = re.findall(r'href=["\']([^"\']*TCB%23\d+[^"\']*\.pdf)[^"\']*["\']', txt, re.I)
        links += re.findall(r'href=["\']([^"\']*TCB#\d+[^"\']*\.pdf)[^"\']*["\']', txt, re.I)
        if not links:
            return []
        # 取编号最大的最新公报
        def key(u):
            m = re.search(r'TCB%23(\d+)|TCB#(\d+)', u)
            return int(m.group(1) or m.group(2)) if m else 0
        url = max(links, key=key)
        pdf = urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=30).read()
        r = PdfReader(urllib.request.urlopen(
            urllib.request.Request(url, headers={"User-Agent": UA}), timeout=30).read() if False else __import__("io").BytesIO(pdf))
        text = "\n".join((p.extract_text() or "") for p in r.pages)
        return self._parse(text, url)

    @staticmethod
    def _parse(text, url):
        """公报文本 → storm-like。返回 [] 解析失败。"""
        name_m = re.search(
            r"Tropical (?:Depression|Storm|Cyclone|Typhoon)\s+([A-Za-z]+)",
            text)
        if not name_m:
            name_m = re.search(r'formerly [“"]([A-Za-z]+)[”"]', text)
        name_en = name_m.group(1).upper() if name_m else "ACTIVE"
        # 实况：中心位置 '(16.4°N, 123.4°E)' 与强度 '55 km/h ... 1002 hPa'
        issued = None
        im = re.search(r"Issued at ([\d:]+ (?:AM|PM)), (\d{1,2} [A-Za-z]+ \d{4})", text)
        if im:
            try:
                issued = datetime.strptime(
                    "%s, %s" % (im.group(1), im.group(2)),
                    "%I:%M %p, %d %B %Y").replace(tzinfo=TZ_BJ)
            except ValueError:
                issued = None
        track = []
        loc_m = re.search(r"\(([\d.]+)\s*°N,\s*([\d.]+)\s*°E\)", text)
        w_m = re.search(r"Maximum sustained winds of ([\d.]+) km/h", text)
        p_m = re.search(r"central pressure of ([\d.]+) hPa", text)
        if loc_m:
            lat, lon = to_num(loc_m.group(1)), to_num(loc_m.group(2))
            w = to_num(w_m.group(1)) if w_m else None
            w = w / 3.6 if w is not None else None
            # 实况时刻：Location of Center (10:00 PM) 观测时间，无则用发布时间
            obs_t = issued
            om = re.search(r"Location of Center \(([\d:]+ (?:AM|PM))\)", text)
            if om and issued:
                try:
                    obs_t = datetime.strptime(
                        "%s %s" % (om.group(1), issued.strftime("%d %B %Y")),
                        "%I:%M %p %d %B %Y").replace(tzinfo=TZ_BJ)
                    # 观测时间可能跨日（发布时间 00:30，观测 23:30 昨日）
                    if (issued - obs_t).total_seconds() > 20 * 3600:
                        obs_t -= timedelta(days=1)
                except ValueError:
                    obs_t = issued
            if lat is not None and lon is not None:
                track.append(make_point(
                    obs_t.isoformat(),
                    lat, lon,
                    wind_ms=w, pressure_hpa=norm_pressure(p_m.group(1)) if p_m else None,
                    grade=grade_from_wind(w)))
        # 预报点：'N-Hour Forecast' 块，块内时间行 + 坐标行
        fpts = []
        for m in re.finditer(
                r"(\d+)-Hour Forecast\s*\n\s*([\d:]+ (?:AM|PM))\s*\n\s*(\d{1,2} [A-Za-z]+ \d{4})\s*\n"
                r"([\d.]+)\s+([\d.]+)\s+[^\n]*?\s+(\d+)\s+(TD|TS|STS|TY|STY|SuperTY)",
                text):
            hours = int(m.group(1))
            try:
                ft = datetime.strptime("%s %s" % (m.group(2), m.group(3)),
                                       "%I:%M %p %d %B %Y").replace(tzinfo=TZ_BJ)
            except ValueError:
                if issued is None:
                    continue
                ft = issued + timedelta(hours=hours)
            lat, lon = to_num(m.group(4)), to_num(m.group(5))
            w = to_num(m.group(6))
            w = w / 3.6 if w is not None else None
            if lat is None or lon is None:
                continue
            fpts.append(make_point(ft.isoformat(), lat, lon,
                                   wind_ms=w, grade=m.group(7)))
        if not track and not fpts:
            return []
        st = {"id": "PAG-ACT", "name_zh": "", "name_en": name_en,
              "is_active": True, "basin": "WP",
              "track": sorted(track, key=lambda p: p["t"])}
        if fpts:
            st["forecasts"] = [{"agency": "PAGASA",
                                "issued_at": (track[-1]["t"] if track else fpts[0]["t"]),
                                "points": sorted(fpts, key=lambda p: p["t"])}]
        else:
            st["forecasts"] = []
        return [st]


# ---------------------------------------------------------------- CWA

class CwaAdapter:
    name = "cwa"
    DATA_URL = "https://www.cwa.gov.tw/Data/js/typhoon/TY_WARN-Data.js"

    def fetch_active(self):
        txt = http_get(self.DATA_URL)
        # 警报期才有 TabBody（含 Analysis/Forecast）；无警报时仅解除信息
        if "TabBody" not in txt:
            return []
        # 提取所有 TabBody_E（英文版）HTML 片段
        bodies = re.findall(r"var TY\d+_TabBody_E = (.*?);\n\nvar TY\d+_", txt, re.S)
        storms = []
        for body in bodies:
            html = body.strip()
            if html.startswith("'"):
                html = html[1:]
            if html.endswith("'"):
                html = html[:-1]
            html = html.replace("'+", "'")
            st = self._parse_html(html)
            if st:
                storms.append(st)
        return storms

    @staticmethod
    def _parse_html(html):
        name_m = re.search(r"([A-Z]{3,})\s*[)]?解除颱風警報|編號第(\d+)號", html)
        if not name_m and "解除" in html:
            return None  # 已解除，无预报
        # 现况：中心位置/气压/风速/暴风半径（繁体中文）
        pos_m = re.search(r"中心位置：</span><span>.*?北緯\s*([\d.]+)\s*度[^東]*東經\s*([\d.]+)\s*度", html)
        press_m = re.search(r"中心氣壓：</span><span>([\d]+)百帕", html)
        wind_m = re.search(r"近中心最大風速：</span><span>.*?每秒\s*([\d]+)\s*公尺", html)
        track = []
        if pos_m:
            lat, lon = to_num(pos_m.group(1)), to_num(pos_m.group(2))
            if lat is not None and lon is not None:
                track.append(make_point(
                    datetime.now(TZ_BJ).replace(minute=0, second=0, microsecond=0).isoformat(),
                    lat, lon,
                    pressure_hpa=norm_pressure(press_m.group(1)) if press_m else None,
                    wind_ms=to_num(wind_m.group(1)) if wind_m else None))
        if not track:
            return None
        st = {"id": "CWA-ACT", "name_zh": "", "name_en": "ACTIVE",
              "is_active": True, "basin": "WP",
              "track": sorted(track, key=lambda p: p["t"])}
        st["forecasts"] = []
        return st


# ---------------------------------------------------------------- JTWC（UCAR b-deck）

class UcarBdeckAdapter:
    """UCAR 镜像 JTWC b-deck：BEST 行 = 实况轨迹（6h 步进）。
    预报（CARQ/OFCL 行）数据少且滞后，不取；JTWC 预报走 nmc 转发。"""

    name = "jtwc-ucar"
    DIR_URL = "https://hurricanes.ral.ucar.edu/repository/data/bdecks_open/%d/"

    def fetch_year(self, year):
        txt = http_get(self.DIR_URL % year)
        files = sorted(set(re.findall(r'href=["\']([^"\']*bwp\d{2}\d{4}\.dat)["\']', txt)))
        storms = []
        for f in files:
            try:
                d = http_get("https://hurricanes.ral.ucar.edu" + f if f.startswith("/")
                             else "https://hurricanes.ral.ucar.edu/repository/data/bdecks_open/%d/%s" % (year, f))
            except Exception:  # noqa: BLE001
                continue
            st = self._parse(d, f)
            if st:
                storms.append(st)
        return storms

    @staticmethod
    def _parse(text, fname):
        track = []
        for line in text.splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 11 or parts[4] != "BEST":
                continue
            # 字段：0=basin 1=num 2=YYYYMMDDHH 4=BEST 6=lat '128N' 7=lon '1783E'
            #        8=风速kt 9=气压 27=名称（约）
            tm_s, lat_s, lon_s = parts[2], parts[6], parts[7]
            if len(tm_s) < 10 or len(lat_s) < 3 or len(lon_s) < 4:
                continue
            try:
                hh = tm_s[8:10] if len(tm_s) >= 10 else "00"
                mm = tm_s[10:12] if len(tm_s) >= 12 else "00"
                # b-deck 时间为 UTC，显式标注时区 → norm_time 转北京时
                t = norm_time("%s-%s-%sT%s:%s:00+00:00" % (
                    tm_s[0:4], tm_s[4:6], tm_s[6:8], hh, mm))
            except Exception:  # noqa: BLE001
                continue
            la = to_num(lat_s[:-1]) / 10.0 if lat_s[:-1] else None
            if lat_s[-1].upper() == "S":
                la = -la
            lo = to_num(lon_s[:-1]) / 10.0 if lon_s[:-1] else None
            if lon_s[-1].upper() == "W":
                lo = -lo
            w_kt = to_num(parts[8])
            w = w_kt * 0.5144 if w_kt is not None else None
            press = norm_pressure(parts[9])
            if t is None or la is None or lo is None:
                continue
            track.append(make_point(t, la, lo, pressure_hpa=press, wind_ms=w,
                                    grade=grade_from_wind(w)))
        if not track:
            return None
        # 名称：取最后出现的非占位名（b-deck 正式命名前的行用 TWELVE/FOUR 等占位）
        name_en = ""
        _PLACE = {"ONE", "TWO", "THREE", "FOUR", "FIVE", "SIX", "SEVEN",
                  "EIGHT", "NINE", "TEN", "ELEVEN", "TWELVE", "THIRTEEN",
                  "FOURTEEN", "FIFTEEN", "SIXTEEN", "SEVENTEEN", "INVEST"}
        for line in text.splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) > 27 and len(parts[27]) > 1:
                nm = parts[27].strip().upper()
                if nm not in _PLACE:
                    name_en = nm
        num_m = re.search(r"bwp(\d{2})\d{4}\.dat", fname)
        num = int(num_m.group(1)) if num_m else 0
        if num >= 90:  # INVEST 未编号低压，非正式台风
            return None
        sid = "WP%02d" % num
        return {"id": sid, "name_zh": "", "name_en": name_en or "UNNAMED",
                "is_active": True, "basin": "WP",
                "track": sorted(track, key=lambda p: p["t"]),
                "forecasts": []}


# ---------------------------------------------------------------- 注册表

ADAPTERS = {
    "kma": KmaAdapter, "hko": HkoAdapter, "pagasa": PagasaAdapter,
    "cwa": CwaAdapter, "jtwc": UcarBdeckAdapter,
}


def fetch_all_extra(year):
    """抓取全部补充源（各自独立失败不互相拖累）。
    返回 [(source_name, storms)]，storms 为空或异常则跳过。"""
    results = []
    for name, cls in ADAPTERS.items():
        try:
            ad = cls()
            if hasattr(ad, "fetch_year"):
                storms = ad.fetch_year(year)
            else:
                storms = ad.fetch_active()
            results.append((name, [s for s in storms if s]))
            print("[%s] %s: %d 个台风" %
                  (datetime.now().strftime("%H:%M:%S"), name, len(storms)), flush=True)
        except Exception as e:  # noqa: BLE001
            print("[%s] %s 失败：%s" %
                  (datetime.now().strftime("%H:%M:%S"), name, e), flush=True)
    return results
