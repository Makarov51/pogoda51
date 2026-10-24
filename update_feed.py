
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

            n = int(g[0])
            cloudiness = {
                0: "Ясно",
                1: "Малооблачно",
                2: "Малооблачно",
                3: "Переменная облачность",
                4: "Переменная облачность",
                5: "Облачно с прояснениями",
                6: "Облачно с прояснениями",
                7: "Облачно с незначительными прояснениями",
                8: "Облачно",
                9: "Неба не видно",
                }
            result["Облачность"] = cloudiness[n]

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

            speed_ms = None
            if ff is not None:
                # iw 0/1 = m/s; iw 3/4 = knots.
                speed_ms = ff if iw in (0, 1) else ff * 0.514444

            if dd == 0 and ff == 0:
                direction = "штиль"
            elif dd == 99:
                direction = "переменное направление"
            elif 1 <= dd <= 36:
                directions = [
                    "С", "ССВ", "СВ", "ВСВ",
                    "В", "ВЮВ", "ЮВ", "ЮЮВ",
                    "Ю", "ЮЮЗ", "ЮЗ", "ЗЮЗ",
                    "З", "ЗСЗ", "СЗ", "ССЗ",
                ]
                degrees = dd * 10
                index = int((degrees + 11.25) // 22.5) % 16
                direction = directions[index]
            else:
                direction = None
   
            if direction is not None and speed_ms is not None:
                result["Ветер"] = (
                    f"{direction}, {speed_ms:.1f} м/с"
                )


    # In FM-12 SYNOP, the current air temperature is positional:
    # station, iRixhVV, Nddff, 1snTTT. If Nddff has ff=99,
    # an additional 00fff wind-speed group may appear before 1snTTT.
    # Do not search the whole report for any group starting with "1":
    # Nddff itself can begin with 1 (e.g. 11906 = cloud 1, wind 90/6).
    temp_pos = wind_pos + 1
    if wind_pos < len(groups) and re.fullmatch(r"\d{5}", groups[wind_pos]):
        wind_group = groups[wind_pos]
        if wind_group[3:5] == "99":
            extra_pos = wind_pos + 1
            if (extra_pos < len(groups)
                    and re.fullmatch(r"00\d{3}", groups[extra_pos])):
                temp_pos = extra_pos + 1

    if temp_pos < len(groups):
        m = re.fullmatch(r"1([01])(\d{3})", groups[temp_pos])
        if m:
            t = int(m.group(2)) / 10
            if m.group(1) == "1":
                t = -t
            # A temperature code outside the plausible range is not published.
            if -60.0 <= t <= 60.0:
                result["Температура воздуха"] = f"{t:+.1f} °C"

    # Pressure and gusts may occur later in the report, so scan all groups
    # for those fields. Temperature is deliberately NOT searched here.
    for g in groups[station_pos + 1:]:
        # Mean sea-level pressure, group 4PPPP.
        m = re.fullmatch(r"4(\d{4})", g)
        if m:
            p = int(m.group(1))
            if p < 5000:
                p += 10000
            result["Давление на уровне моря"] = f"{p / 10 * 0.7500617:.0f} мм рт. ст."

        # Maximum wind speed: SYNOP group 911ff, speed in m/s.
        m = re.fullmatch(r"911(\d{2})", g)
        if m:
            gust = int(m.group(1))
            if gust != 99:
                result["Порывы ветра"] = f"{gust} м/с"

    return result
    
    


def make_item(dt, synop):
    local = dt.astimezone(MSK)
    values = decode(synop)
    title = f"Мурманск — {local:%d.%m.%Y %H:%M} МСК"

    body = [
        f"<p><b>Погода на ГМС Мурманск — "
        f"{local:%d.%m.%Y %H:%M} МСК</b></p>",
        "<ul>",
    ]

    
    for key in (
        "Температура воздуха",
        "Ветер",
        "Давление на уровне моря",
    ):
        if key == "Температура воздуха" and key in values:
            temperature = values[key]
            cloudiness = values.get("Облачность")

            if cloudiness:
                temperature += f", {cloudiness.lower()}"

            body.append(
                f"<li>{escape(key)}: "
                f"{escape(temperature)}</li>"
            )
        elif key == "Ветер" and key in values:
            wind_text = values[key]
            gust_text = values.get("Порывы ветра")

            if gust_text:
                wind_text += f", порывы до {gust_text}"

            body.append(
                f"<li>{escape(key)}: "
                f"{escape(wind_text)}</li>"
            )
        elif key in values:
            body.append(
                f"<li>{escape(key)}: "
                f"{escape(values[key])}</li>"
            )



    body.append("</ul>")

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
