#!/usr/bin/env python3
"""Fetch departure and arrival times from official OEBB and Wiener Linien APIs.

Examples:
  python train_times.py oebb "Wien Hbf (Bahnsteige 3-12)"
  python train_times.py wl 147
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from typing import Any


OEBB_MGATE_URL = "https://fahrplan.oebb.at/bin/mgate.exe"
WL_MONITOR_URL = "https://www.wienerlinien.at/ogd_realtime/monitor"
USER_AGENT = "oebb-monitor/1.0"


def build_oebb_body(requests: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "auth": {"type": "AID", "aid": "5vHavmuWPWIfetEe"},
        "client": {
            "id": "OEBB",
            "type": "WEB",
            "name": "webapp",
            "l": "vs_webapp",
            "v": "2.19.1",
        },
        "ext": "OEBB.14",
        "ver": "1.80",
        "lang": "deu",
        "svcReqL": requests,
    }


def http_json(url: str, *, data: dict[str, Any] | None = None) -> dict[str, Any]:
    encoded = None if data is None else json.dumps(data).encode("utf-8")
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
    }
    if encoded is not None:
        headers["Content-Type"] = "application/json"

    request = urllib.request.Request(url, data=encoded, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def parse_hafas_timestamp(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.000%z")


def parse_compact_time(date_str: str, time_str: str, tz_offset_minutes: int | None) -> datetime:
    base = datetime.strptime(date_str + time_str, "%Y%m%d%H%M%S")
    if tz_offset_minutes is None:
        return base
    sign = "+" if tz_offset_minutes >= 0 else "-"
    total = abs(tz_offset_minutes)
    offset = f"{sign}{total // 60:02d}{total % 60:02d}"
    return datetime.strptime(base.strftime("%Y%m%d%H%M%S") + offset, "%Y%m%d%H%M%S%z")


def simplify_text(value: str) -> str:
    value = value.casefold()
    value = value.replace("strasse", "straße")
    value = re.sub(r"\(.*?\)", "", value)
    value = re.sub(r"[^a-z0-9äöüß]+", " ", value)
    return " ".join(value.split())


def same_destination(left: str, right: str) -> bool:
    a = simplify_text(left)
    b = simplify_text(right)
    if a == b or a in b or b in a:
        return True
    common_tokens = {
        "wien",
        "u",
        "bahnhof",
        "bahnhst",
        "bahnhofs",
        "tiefgeschoß",
        "tiefgeschoss",
        "schleife",
        "sud",
        "nord",
        "ost",
        "west",
        "zentrum",
    }
    a_tokens = {token for token in a.split() if token not in common_tokens and len(token) > 2}
    b_tokens = {token for token in b.split() if token not in common_tokens and len(token) > 2}
    return bool(a_tokens and b_tokens and a_tokens.intersection(b_tokens))


def format_line_name(product: dict[str, Any]) -> str:
    name = product.get("name") or ""
    number = product.get("number") or ""
    if name and number and number in name:
        return name
    if name and number:
        return f"{name} {number}"
    return name or number or "Unknown"


def time_from_stop(stop: dict[str, Any], prefix: str, journey_date: str) -> datetime | None:
    real_key = f"{prefix}TimeR"
    sched_key = f"{prefix}TimeS"
    tz_key = f"{prefix[0]}TZOffset"
    value = stop.get(real_key) or stop.get(sched_key)
    if not value:
        return None
    return parse_compact_time(journey_date, value, stop.get(tz_key))


def oebb_loc_match(name: str) -> dict[str, Any]:
    payload = build_oebb_body(
        [{"meth": "LocMatch", "req": {"input": {"field": "S", "loc": {"name": name, "type": "S"}}}}]
    )
    data = http_json(OEBB_MGATE_URL, data=payload)
    service = data["svcResL"][0]
    if service.get("err") != "OK":
        raise RuntimeError(f"OEBB location lookup failed: {service.get('err')}")
    locations = service["res"]["match"].get("locL", [])
    if not locations:
        raise RuntimeError(f"No OEBB station/stop match found for '{name}'.")
    exact = simplify_text(name)
    for location in locations:
        if simplify_text(location.get("name", "")) == exact:
            return location
    return locations[0]


def oebb_station_board(location: dict[str, Any], when: datetime | None, limit: int) -> dict[str, Any]:
    if when is None:
        when = datetime.now().astimezone()
    payload = build_oebb_body(
        [
            {
                "meth": "StationBoard",
                "req": {
                    "type": "DEP",
                    "stbLoc": {
                        "type": "S",
                        "name": location["name"],
                        "lid": location.get("lid"),
                    },
                    "date": when.strftime("%Y%m%d"),
                    "time": when.strftime("%H%M%S"),
                    "maxJny": limit,
                },
            }
        ]
    )
    data = http_json(OEBB_MGATE_URL, data=payload)
    service = data["svcResL"][0]
    if service.get("err") != "OK":
        raise RuntimeError(f"OEBB station board failed: {service.get('err')}")
    return service["res"]


def oebb_journey_details(jid: str, journey_date: str) -> dict[str, Any]:
    payload = build_oebb_body([{"meth": "JourneyDetails", "req": {"jid": jid, "date": journey_date}}])
    data = http_json(OEBB_MGATE_URL, data=payload)
    service = data["svcResL"][0]
    if service.get("err") != "OK":
        raise RuntimeError(f"OEBB journey details failed: {service.get('err')}")
    return service["res"]


def extract_oebb_arrival(jid: str, journey_date: str) -> datetime | None:
    details = oebb_journey_details(jid, journey_date)
    journey = details["journey"]
    last_stop = journey["stopL"][-1]
    return time_from_stop(last_stop, "a", journey.get("date", journey_date))


@dataclass
class DepartureRow:
    departure: datetime
    arrival: datetime | None
    line_name: str
    location: str

    def render(self) -> str:
        dep = self.departure.strftime("%H:%M")
        arr = self.arrival.strftime("%H:%M") if self.arrival else "--:--"
        return f"Dep. {dep} Arr. {arr}  | {self.line_name} | {self.location}"

    def as_dict(self) -> dict[str, str | None]:
        return {
            "departure": self.departure.strftime("%H:%M"),
            "arrival": self.arrival.strftime("%H:%M") if self.arrival else None,
            "line_name": self.line_name,
            "location": self.location,
            "display": self.render(),
        }


def get_oebb_rows(name: str, limit: int) -> list[DepartureRow]:
    board = oebb_station_board({"name": name}, None, limit)
    common = board["common"]
    products = common["prodL"]
    location_index = board.get("locRefL", [0])[0]
    location_name = common["locL"][location_index]["name"]
    rows: list[DepartureRow] = []

    for journey in board.get("jnyL", [])[:limit]:
        stop = journey["stbStop"]
        departure = time_from_stop(stop, "d", journey["date"])
        if departure is None:
            continue
        product = products[journey["prodX"]]
        arrival = extract_oebb_arrival(journey["jid"], journey["date"])
        rows.append(
            DepartureRow(
                departure=departure,
                arrival=arrival,
                line_name=format_line_name(product),
                location=location_name,
            )
        )

    return rows


def get_wl_monitor(stop_id: int) -> dict[str, Any]:
    url = f"{WL_MONITOR_URL}?{urllib.parse.urlencode({'stopId': stop_id})}"
    data = http_json(url)
    monitors = data.get("data", {}).get("monitors", [])
    if not monitors:
        raise RuntimeError(f"No Wiener Linien monitor data for stopId={stop_id}.")
    return monitors[0]


def match_wl_row_to_oebb(
    stop_name: str,
    vehicle_name: str,
    destination: str,
    departure: datetime,
    search_limit: int,
) -> tuple[str, datetime | None]:
    location = oebb_loc_match(stop_name)
    board = oebb_station_board(location, departure, search_limit)
    common = board["common"]
    products = common["prodL"]

    best_journey: dict[str, Any] | None = None
    best_product: dict[str, Any] | None = None
    best_score: tuple[int, int] | None = None

    for journey in board.get("jnyL", []):
        stop = journey["stbStop"]
        board_departure = time_from_stop(stop, "d", journey["date"])
        if board_departure is None:
            continue

        product = products[journey["prodX"]]
        product_name = format_line_name(product)
        product_number = str(product.get("number") or "")

        if vehicle_name != product_name and vehicle_name != product_number:
            continue
        if not same_destination(destination, journey.get("dirTxt", "")):
            continue

        time_diff = abs(int((board_departure - departure).total_seconds()))
        score = (time_diff, 0 if same_destination(destination, journey.get("dirTxt", "")) else 1)
        if best_score is None or score < best_score:
            best_score = score
            best_journey = journey
            best_product = product

    if best_journey is None or best_product is None:
        return vehicle_name, None

    arrival = extract_oebb_arrival(best_journey["jid"], best_journey["date"])
    return format_line_name(best_product), arrival


def get_wl_rows(stop_id: int, limit: int) -> list[DepartureRow]:
    monitor = get_wl_monitor(stop_id)
    stop_name = monitor["locationStop"]["properties"]["title"]
    rows: list[DepartureRow] = []

    for line in monitor.get("lines", []):
        departures = line.get("departures", {}).get("departure", [])
        for departure_info in departures:
            time_info = departure_info.get("departureTime", {})
            timestamp = time_info.get("timeReal") or time_info.get("timePlanned")
            if not timestamp:
                continue

            vehicle = departure_info.get("vehicle", {})
            vehicle_name = vehicle.get("name") or line.get("name") or "Unknown"
            destination = vehicle.get("towards") or line.get("towards") or ""
            departure = parse_hafas_timestamp(timestamp)
            display_name, arrival = match_wl_row_to_oebb(
                stop_name=stop_name,
                vehicle_name=vehicle_name,
                destination=destination,
                departure=departure,
                search_limit=max(limit * 4, 12),
            )
            rows.append(
                DepartureRow(
                    departure=departure,
                    arrival=arrival,
                    line_name=display_name,
                    location=stop_name,
                )
            )
            if len(rows) >= limit:
                return rows

    return rows


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Show departures/arrivals using official OEBB and Wiener Linien APIs."
    )
    parser.add_argument("provider", choices=["oebb", "wl"], help="Data source to query.")
    parser.add_argument(
        "target",
        help="For 'oebb': station/stop name. For 'wl': monitor stopId.",
    )
    parser.add_argument("--limit", type=int, default=5, help="Number of rows to print. Default: 5")
    parser.add_argument("--json", action="store_true", help="Print structured JSON instead of text rows.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.provider == "oebb":
            rows = get_oebb_rows(args.target, args.limit)
        else:
            rows = get_wl_rows(int(args.target), args.limit)

        if not rows:
            print("No departures found.")
            return 1

        if args.json:
            print(json.dumps([row.as_dict() for row in rows], ensure_ascii=False))
            return 0

        for row in rows:
            print(row.render())
        return 0
    except ValueError:
        print("For provider 'wl', target must be a numeric stopId.", file=sys.stderr)
        return 2
    except (RuntimeError, urllib.error.URLError, KeyError, IndexError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
