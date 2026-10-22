#!/usr/bin/env python3
"""Fetch recent raw SYNOP reports from OGIMET and generate an RSS 2.0 feed."""
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from html import escape
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import re
import xml.etree.ElementTree as ET

STATION = "22113"
OUT = Path("docs/feed.xml")
UTC = timezone.utc
LOCAL = timezone(timedelta(hours=3))  # Murmansk uses Moscow time (UTC+3)
BASE = "https://www.ogimet.com/cgi-bin/getsynop"

def parse_report_line(line):
    # CSV metadata fields: station, year, month, day, hour, minute, SYNOP text.
    parts = line.strip().split(",", 6)
    if len(parts) < 7:
        return None
    station, year, month, day, hour, minute, synop = parts[:7]
    if station.strip() != STATION:
        return None
    try:
        dt = datetime(int(year), int(month), int(day), int(hour), int(minute), tzinfo=UTC)
    except ValueError:
        return None
    return dt, synop.strip()

def signed_tenths(sign_code, digits):
    value = int(digits) / 10.0
    return -value if sign_code == "1" else value

def fmt(value, suffix=""):
    if value is None:
        return "нет данных"
    return f"{value:+.1f}{suffix}" if suffix == " °C" else f"{value:.1f}{suffix}"

def decode(synop):
    # Extract only standard groups we can identify without guessing.
    result = {}
    groups = synop.replace("=", " ").split()

    for g in groups:
        m = re.fullmatch(r"1([01])(\d{3})", g)
        if m:
            result["Температура"] = fmt(signed_tenths(m.group(1), m.group(2)), " °C")
        m = re.fullmatch(r"2([01])(\d{3})", g)
        if m:
            result["Точка росы"] = fmt(signed_tenths(m.group(1), m.group(2)), " °C")
        # Pressure at station level (3) and mean sea-level pressure (4), tenths hPa.
        m = re.fullmatch(r"([34])(\d{4})", g)
        if m:
            n = int(m.group(2))
            pressure = (1000 + n / 10.0) if n < 5000 else n / 10.0
            key = "Давление на станции" if m.group(1) == "3" else "Давление на уровне моря"
            result[key] = fmt(pressure, " гПа")
        # Common surface wind group: Nddff; dd in tens of degrees, ff in m/s.
        m = re.fullmatch(r"([0-9])(\d{2})(\d{2})", g)
        if m and g.startswith("0") and not g.startswith(("00", "01", "02", "03", "04", "05", "06", "07", "08", "09")):
            pass
        # Group 00fff may carry wind speed in m/s in some national variants.
        m = re.fullmatch(r"00(\d{3})", g)
        if m:
            result["Ветер"] = f"{int(m.group(1))} м/с"
    return result

def build_item(dt, synop):
    decoded = decode(synop)
    local_dt = dt.astimezone(LOCAL)
    title = f"Мурманск — наблюдение {local_dt:%d.%m.%Y %H:%M} (МСК)"
    lines = [
        f"<p><b>Станция:</b> Мурманск, ВМО {STATION}</p>",
        f"<p><b>Время наблюдения:</b> {dt:%Y-%m-%d %H:%M} UTC / {local_dt:%Y-%m-%d %H:%M} МСК</p>",
    ]
    if decoded:
        lines.append("<ul>" + "".join(f"<li><b>{escape(k)}:</b> {escape(v)}</li>" for k, v in decoded.items()) + "</ul>")
    lines.append(f"<p><b>Исходный SYNOP:</b><br/><code>{escape(synop)}</code></p>")
    lines.append("<p>Источник: OGIMET; фактическое наземное наблюдение, не прогноз.</p>")
    description = "".join(lines)
    item = ET.Element("item")
    ET.SubElement(item, "title").text = title
    ET.SubElement(item, "link").text = "https://www.ogimet.com/synops.phtml.en"
    ET.SubElement(item, "guid", {"isPermaLink": "false"}).text = f"{STATION}-{dt:%Y%m%d%H%M}-{synop}"
    ET.SubElement(item, "pubDate").text = format_datetime(dt)
    ET.SubElement(item, "description").text = description
    return item

def main():
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    start = now - timedelta(days=3)
    params = {
        "block": STATION,
        "begin": start.strftime("%Y%m%d%H%M"),
        "end": now.strftime("%Y%m%d%H%M"),
        "header": "yes",
    }
    url = BASE + "?" + urlencode(params)
    req = Request(url, headers={"User-Agent": "Murmansk-SYNOP-RSS/1.0 (personal informational feed)"})
    with urlopen(req, timeout=45) as response:
        body = response.read().decode("utf-8", errors="replace")
    reports = {}
    for line in body.splitlines():
        parsed = parse_report_line(line)
        if parsed:
            dt, synop = parsed
            if synop:
                reports[(dt, synop)] = None
    if not reports:
        raise RuntimeError("OGIMET returned no usable SYNOP reports; preserving existing feed.")

    root = ET.Element("rss", {"version": "2.0", "{" + "http://www.w3.org/XML/1998/namespace" + "}lang": "ru"})
    channel = ET.SubElement(root, "channel")
    ET.SubElement(channel, "title").text = "Фактическая погода — Мурманск, ВМО 22113"
    ET.SubElement(channel, "link").text = "https://www.ogimet.com/synops.phtml.en"
    ET.SubElement(channel, "description").text = "Фактические наземные SYNOP-наблюдения станции Мурманск. Не прогноз. Источник: OGIMET."
    ET.SubElement(channel, "language").text = "ru"
    ET.SubElement(channel, "lastBuildDate").text = format_datetime(now)
    ET.SubElement(channel, "ttl").text = "60"
    for dt, synop in sorted(reports, reverse=True):
        channel.append(build_item(dt, synop))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    temp = OUT.with_suffix(".xml.tmp")
    tree.write(temp, encoding="utf-8", xml_declaration=True)
    temp.replace(OUT)
    print(f"Wrote {len(reports)} reports to {OUT}")

if __name__ == "__main__":
    main()
