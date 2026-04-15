#!/usr/bin/env python3
"""Web-facing helpers for station suggestions and journey details."""

from __future__ import annotations

import json
import re
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
    for source, target in {"ß": "ss", "ä": "ae", "ö": "oe", "ü": "ue"}.items():
        value = value.replace(source, target)
    value = re.sub(r"\(.*?\)", "", value)
    value = re.sub(r"[^a-z0-9]+", " ", value)
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


def oebb_location_suggestions(name: str, limit: int = 8) -> list[dict[str, Any]]:
    payload = build_oebb_body(
        [{"meth": "LocMatch", "req": {"input": {"field": "S", "loc": {"name": name, "type": "S"}}}}]
    )
    data = http_json(OEBB_MGATE_URL, data=payload)
    service = data["svcResL"][0]
    if service.get("err") != "OK":
        raise RuntimeError(f"OEBB location lookup failed: {service.get('err')}")
    return service["res"]["match"].get("locL", [])[:limit]


def get_autocomplete_suggestions(provider: str, query: str, limit: int = 8) -> list[dict[str, str]]:
    if provider != "oebb" or len(query.strip()) < 2:
        return []
    return [{"label": item["name"], "value": item["name"]} for item in oebb_location_suggestions(query, limit)]


def oebb_loc_match(name: str) -> dict[str, Any]:
    locations = oebb_location_suggestions(name, 8)
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


def format_platform(stop: dict[str, Any], prefix: str) -> str | None:
    platform = stop.get(f"{prefix}PltfS")
    if isinstance(platform, dict):
        return platform.get("txt")
    return None


@dataclass
class StopDetail:
    name: str
    arrival: str | None
    departure: str | None
    platform: str | None

    def as_dict(self) -> dict[str, str | None]:
        return {
            "name": self.name,
            "arrival": self.arrival,
            "departure": self.departure,
            "platform": self.platform,
        }


def build_stop_details(details: dict[str, Any]) -> list[StopDetail]:
    journey = details["journey"]
    locations = details["common"]["locL"]
    stops: list[StopDetail] = []

    for stop in journey.get("stopL", []):
        location = locations[stop["locX"]]
        arrival_time = time_from_stop(stop, "a", journey["date"])
        departure_time = time_from_stop(stop, "d", journey["date"])
        platform = format_platform(stop, "d") or format_platform(stop, "a")
        stops.append(
            StopDetail(
                name=location["name"],
                arrival=arrival_time.strftime("%H:%M") if arrival_time else None,
                departure=departure_time.strftime("%H:%M") if departure_time else None,
                platform=platform,
            )
        )

    return stops


def get_oebb_journey_summary(jid: str, journey_date: str) -> tuple[datetime | None, list[StopDetail]]:
    details = oebb_journey_details(jid, journey_date)
    journey = details["journey"]
    last_stop = journey["stopL"][-1]
    arrival = time_from_stop(last_stop, "a", journey.get("date", journey_date))
    return arrival, build_stop_details(details)


@dataclass
class DepartureRow:
    departure: datetime
    arrival: datetime | None
    line_name: str
    location: str
    stop_details: list[StopDetail]

    def render(self) -> str:
        dep = self.departure.strftime("%H:%M")
        arr = self.arrival.strftime("%H:%M") if self.arrival else "--:--"
        return f"Dep. {dep} Arr. {arr}  | {self.line_name} | {self.location}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "departure": self.departure.strftime("%H:%M"),
            "arrival": self.arrival.strftime("%H:%M") if self.arrival else None,
            "line_name": self.line_name,
            "location": self.location,
            "display": self.render(),
            "stop_details": [stop.as_dict() for stop in self.stop_details],
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
        arrival, stop_details = get_oebb_journey_summary(journey["jid"], journey["date"])
        rows.append(
            DepartureRow(
                departure=departure,
                arrival=arrival,
                line_name=format_line_name(product),
                location=location_name,
                stop_details=stop_details,
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
) -> tuple[str, datetime | None, list[StopDetail]]:
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
        return vehicle_name, None, []

    arrival, stop_details = get_oebb_journey_summary(best_journey["jid"], best_journey["date"])
    return format_line_name(best_product), arrival, stop_details


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
            display_name, arrival, stop_details = match_wl_row_to_oebb(
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
                    stop_details=stop_details,
                )
            )
            if len(rows) >= limit:
                return rows

    return rows
