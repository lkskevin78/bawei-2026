#!/usr/bin/env python3
"""自動更新颱風巴威(BAVI)路徑資料 typhoon-data.json。
資料來源：中央氣象署 https://www.cwa.gov.tw/Data/js/typhoon/TY_NEWS-Data.js（公開、免金鑰）
"""
import json
import math
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_FILE = REPO_ROOT / "typhoon-data.json"
SOURCE_URL = "https://www.cwa.gov.tw/Data/js/typhoon/TY_NEWS-Data.js"
TAIPEI = ZoneInfo("Asia/Taipei")

# 排程只在這個日期範圍內生效，超出範圍就不動作（由 Kevin 指定：排到7/10為止）
WINDOW_START = (7, 7)
WINDOW_END = (7, 10)


def in_window(now):
    md = (now.month, now.day)
    return WINDOW_START <= md <= WINDOW_END


def it_from_hpa(hpa):
    v = (1015 - hpa) / 120
    return round(max(0.0, min(1.0, v)), 3)


def extract_html_block(text):
    m = re.search(r"TY_LIST_2\['C'\] = ''\+(.*?)TY_LIST_1\['E'\]", text, re.S)
    if not m:
        return None
    parts = []
    for line in m.group(1).splitlines():
        line = line.strip()
        if line.startswith("'"):
            line = line[1:]
        if line.endswith("'+"):
            line = line[:-2]
        elif line.endswith("';"):
            line = line[:-2]
        parts.append(line)
    return "".join(parts)


def parse_current(html):
    m = re.search(
        r'<span class="now">現況</span><p>(\d{4})年(\d{2})月(\d{2})日(\d{2})時</p>'
        r'.*?<li>中心位置在北緯 ([\d.]+) 度，東經 ([\d.]+) 度</li>'
        r'.*?<li>中心氣壓 (\d+)百帕</li><li>近中心最大風速每秒 (\d+) 公尺</li>',
        html,
    )
    if not m:
        return None
    year, mon, day, hour, lat, lon, hpa, wind = m.groups()
    return {
        "year": int(year), "month": int(mon), "day": int(day), "hour": int(hour),
        "lat": float(lat), "lon": float(lon), "hpa": int(hpa), "wind": int(wind),
    }


def parse_forecast(html):
    rows = re.findall(
        r'<li>預測 (\d+)月(\d+)日(\d+)時</li><li>中心位置在北緯 ([\d.]+) 度，東經 ([\d.]+) 度</li>'
        r'<li>中心氣壓(\d+)百帕</li><li>近中心最大風速每秒 (\d+) 公尺</li>'
        r'(?:<li>瞬間最大陣風每秒 \d+ 公尺</li>)?'
        r'(?:<li>七級風暴風半徑 \d+ 公里</li>)?'
        r'(?:<li>十級風暴風半徑 \d+ 公里</li>)?'
        r'<li>70%機率半徑 (\d+) 公里</li>',
        html,
    )
    out = []
    for mon, day, hour, lat, lon, hpa, wind, radius in rows:
        out.append({
            "month": int(mon), "day": int(day), "hour": int(hour),
            "lat": float(lat), "lon": float(lon), "hpa": int(hpa),
            "wind": int(wind), "radius": int(radius),
        })
    return out


def branch_offset(lon, lat, radius_km, lon_factor, lat_factor):
    off_lon = radius_km / (111 * math.cos(math.radians(lat))) * lon_factor
    off_lat = radius_km / 111 * lat_factor
    return off_lon, off_lat


def main():
    now = datetime.now(TAIPEI)
    if not in_window(now):
        print(f"目前台北時間 {now:%Y-%m-%d %H:%M} 已超出排程視窗（7/7-7/10），不執行更新。")
        return 0

    try:
        result = subprocess.run(
            ["curl", "-fsSL", "-A", "Mozilla/5.0", "--max-time", "20", SOURCE_URL],
            capture_output=True, check=True,
        )
        text = result.stdout.decode("utf-8")
    except Exception as e:
        print(f"抓取氣象署資料失敗：{e}，本次跳過不更新。")
        return 0

    if "BAVI" not in text or "TY_LIST_2" not in text:
        print("氣象署頁面已無巴威颱風消息（警報可能已解除或颱風已消散），不更新網站，建議終止此排程。")
        return 0

    html = extract_html_block(text)
    if not html:
        print("無法解析氣象署資料格式（頁面結構可能已變動），本次跳過不更新。")
        return 0

    cur = parse_current(html)
    forecast = parse_forecast(html)
    if not cur or not forecast:
        print("解析氣象署觀測/預測資料失敗，本次跳過不更新。")
        return 0

    data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    today_date_str = f"{cur['month']}/{cur['day']}"
    today_md = (cur["month"], cur["day"])

    observed = data["observed"]
    cur_it = it_from_hpa(cur["hpa"])
    if observed and observed[-1]["date"] == today_date_str:
        observed[-1].update({"lon": cur["lon"], "lat": cur["lat"], "it": cur_it, "lbl": "現況"})
    else:
        observed.append({"lon": cur["lon"], "lat": cur["lat"], "date": today_date_str, "it": cur_it, "lbl": "現況"})

    # 只保留「今天之後」的預測點，同一天多筆時取當天最後一筆（較接近當天實況）
    by_date = {}
    for row in forecast:
        md = (row["month"], row["day"])
        if md <= today_md:
            continue
        by_date[f"{row['month']}/{row['day']}"] = row
    main_forecast = [by_date[d] for d in sorted(by_date, key=lambda d: tuple(map(int, d.split("/"))))]

    if not main_forecast:
        print("氣象署預測資料未涵蓋未來日期，僅更新現況觀測點。")
    else:
        s2 = []
        s1 = []
        s3 = []
        for row in main_forecast:
            date_str = f"{row['month']}/{row['day']}"
            it = it_from_hpa(row["hpa"])
            s2.append({"lon": row["lon"], "lat": row["lat"], "date": date_str, "it": it, "lbl": ""})
            off_lon, off_lat = branch_offset(row["lon"], row["lat"], row["radius"], 0.32, 0.12)
            s1.append({
                "lon": round(row["lon"] - off_lon, 1), "lat": round(row["lat"] - off_lat, 1),
                "date": date_str, "it": round(min(1.0, it + 0.03), 3), "lbl": "",
            })
            s3.append({
                "lon": round(row["lon"] + off_lon, 1), "lat": round(row["lat"] + off_lat, 1),
                "date": date_str, "it": round(max(0.0, it - 0.15), 3), "lbl": "",
            })
        # 保留原本手動下的關鍵地標文字（若日期對得上），其餘留空
        for new_list, old_key in ((s1, "1"), (s2, "2"), (s3, "3")):
            old_forecast = {p["date"]: p.get("lbl", "") for p in data["scenarios"][old_key].get("forecast", [])}
            for p in new_list:
                if p["date"] in old_forecast and old_forecast[p["date"]]:
                    p["lbl"] = old_forecast[p["date"]]

        data["scenarios"]["1"]["forecast"] = s1
        data["scenarios"]["2"]["forecast"] = s2
        data["scenarios"]["3"]["forecast"] = s3

    data["updated"] = f"{cur['year']}/{cur['month']:02d}/{cur['day']:02d} {cur['hour']:02d}時"
    data["observed"] = observed

    DATA_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"已更新 typhoon-data.json，資料時間 {data['updated']}，現況 {cur['lat']}N {cur['lon']}E {cur['hpa']}hPa。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
