
#!/usr/bin/env python3
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
MSK = timezone(timedelta(hours=3))
BASE = "https://www.ogimet.com/cgi-bin/getsynop"


def parse_line(line):
    parts = line.strip().split(",", 6)
    if len(parts) != 7 or parts[0].strip() != STATION:
        return None
    try:
        dt = datetime(
            int(parts[1]), int(parts[2]), int(parts[3]),
            int(parts[4]), int(parts[5]), tzinfo=UTC
        )
    except ValueError:
        return None
    return dt, parts[6].strip()


def decode(synop):
    groups = synop.replace("=", " ").split()
    result = {}

    try:
        station_pos = groups.index(STATION)
    except ValueError:
        return result

    # YYGGiw: the last digit specifies wind units.
    iw = None
    if len(groups) > 1 and re.fullmatch(r"\d{5}", groups[1]):
        iw = int(groups[1][-1])

    # Nddff follows iRixhVV, which is immediately after station ID.
    wind_pos = station_pos + 2
    if wind_pos < len(groups):
        g = groups[wind_pos]
        if re.fullmatch(r"\d{5}", g) and iw in (0, 1, 3, 4):
            dd = int(g[1:3])
            ff = int(g[3:5])

            # 00fff gives full speed when ff is coded as 99.
            if ff == 99:
                extra_pos = wind_pos + 1
                if (extra_pos < len(groups)
                        and re.fullmatch(r"00\d{3}", groups[extra_pos])):
                    ff = int(groups[extra_pos][2:5])
                else:
                    ff = None

            if ff is not None:
                # iw 0/1 = m/s; iw 3/4 = knots.
                speed_ms = ff if iw in (0, 1) else ff * 0.514444
                if dd == 0 and ff == 0:
                    direction = "штиль"
                elif dd == 99:
                    direction = "переменное направление"
                elif 1 <= dd <= 36:
                    direction = f"{dd * 10:03d}°"
                else:
                    direction = None

                if direction is not None:
                    result["Ветер"] = (
                        f"{direction}, {speed_ms:.1f} м/с"
                    )

    # Decode only explicitly present groups; ///// means missing.
    for g in groups[station_pos + 1:]:
        m = re.fullmatch(r"1([01])(\d{3})", g)
        if m:
            t = int(m.group(2)) / 10
            if m.group(1) == "1":
                t = -t
            result["Температура воздуха"] = f"{t:+.1f} °C"

        # Station pressure, group 3P0P0P0P0.
        m = re.fullmatch(r"3(\d{4})", g)
        if m:
            p = int(m.group(1))
            if p < 5000:
                p += 10000
            result["Давление на станции"] = f"{p / 10 * 0.7500617:.0f} мм рт. ст."

        # Mean sea-level pressure, group 4PPPP.
        m = re.fullmatch(r"4(\d{4})", g)
        if m:
            p = int(m.group(1))
            if p < 5000:
                p += 10000
            result["Давление на уровне моря"] = f"{p / 10 * 0.7500617:.0f} мм рт. ст."

    return result


def make_item(dt, synop):
    local = dt.astimezone(MSK)
    values = decode(synop)
    title = f"Мурманск — {local:%d.%m.%Y %H:%M} МСК"

    body = [
        f"<p><b>Станция:</b> Мурманск, ВМО {STATION}</p>",
        f"<p><b>Время наблюдения:</b> "
        f"{local:%d.%m.%Y %H:%M} МСК</p>",
        "<ul>",
    ]

    for key in (
        "Температура воздуха",
        "Ветер",
        "Давление на станции",
        "Давление на уровне моря",
    ):
        if key in values:
            body.append(
                f"<li><b>{escape(key)}:</b> "
                f"{escape(values[key])}</li>"
            )

    body.append("</ul>")
    body.append(
        "<p><b>Исходный SYNOP:</b><br><code>"
        + escape(synop)
        + "</code></p>"
    )
    body.append(
        "<p>Источник: OGIMET. Фактическое наблюдение, не прогноз.</p>"
    )

    item = ET.Element("item")
    ET.SubElement(item, "title").text = title
    ET.SubElement(item, "link").text = (
        "https://www.ogimet.com/synops.phtml.en"
    )
    ET.SubElement(item, "guid", {"isPermaLink": "false"}).text = (
        f"{STATION}-{dt:%Y%m%d%H%M}-{synop}"
    )
    ET.SubElement(item, "pubDate").text = format_datetime(dt)
    ET.SubElement(item, "description").text = "".join(body)
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
    request = Request(
        url,
        headers={"User-Agent": "Murmansk-SYNOP-RSS/1.1"}
    )

    with urlopen(request, timeout=45) as response:
        raw = response.read().decode("utf-8", errors="replace")

    reports = {}
    for line in raw.splitlines():
        parsed = parse_line(line)
        if parsed:
            dt, synop = parsed
            if synop and "NIL" not in synop.upper():
                reports[(dt, synop)] = None

    if not reports:
        raise RuntimeError(
            "OGIMET не вернул пригодных сообщений. "
            "Существующая лента не заменена."
        )

    root = ET.Element("rss", {"version": "2.0"})
    root.set(
        "{http://www.w3.org/XML/1998/namespace}lang", "ru"
    )
    channel = ET.SubElement(root, "channel")
    ET.SubElement(channel, "title").text = (
        "Фактическая погода — Мурманск, ВМО 22113"
    )
    ET.SubElement(channel, "link").text = (
        "https://www.ogimet.com/synops.phtml.en"
    )
    ET.SubElement(channel, "description").text = (
        "Фактические наземные SYNOP-наблюдения. "
        "Температура, ветер и давление. Источник: OGIMET."
    )
    ET.SubElement(channel, "language").text = "ru"
    ET.SubElement(channel, "lastBuildDate").text = format_datetime(now)
    ET.SubElement(channel, "ttl").text = "60"

    for dt, synop in sorted(reports, reverse=True):
        channel.append(make_item(dt, synop))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    temp = OUT.with_suffix(".xml.tmp")
    tree.write(temp, encoding="utf-8", xml_declaration=True)
    temp.replace(OUT)
    print(f"RSS обновлён: {len(reports)} сообщений")


if __name__ == "__main__":
    main()
