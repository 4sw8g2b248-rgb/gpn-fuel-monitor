
import os
import time
import http.cookiejar
import urllib.request
from datetime import datetime, timezone

from flask import Flask, jsonify


app = Flask(__name__)

BASE = "https://gpnbonus.ru"
PAGE = BASE + "/fuel/refuel-map"
STATIONS_API = BASE + "/api/stations/list"

TARGET_NUMBERS = {"62", "65", "66", "111"}
TARGET_CITY_TOKEN = "каменск"

TRACKED_FUELS = {
    "12": "АИ-95",
    "421": "G-95",
    "100032": "G-100",
}

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/152.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    "X-Requested-With": "XMLHttpRequest",
}


def normalize_station_number(value):
    raw = str(value or "").strip()
    normalized = raw.lstrip("0")
    return normalized or "0"


def make_opener():
    cookies = http.cookiejar.CookieJar()
    return urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(cookies)
    )


def prime_session(opener):
    req = urllib.request.Request(
        PAGE,
        headers={
            **DEFAULT_HEADERS,
            "Accept": (
                "text/html,application/xhtml+xml,"
                "application/xml;q=0.9,*/*;q=0.8"
            ),
        },
        method="GET",
    )

    with opener.open(req, timeout=30) as response:
        response.read()


def post_json(opener, url, payload=None):
    body = json.dumps(payload or {}).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=body,
        headers={
            **DEFAULT_HEADERS,
            "Content-Type": "application/json",
            "Referer": PAGE,
            "Origin": BASE,
        },
        method="POST",
    )

    with opener.open(req, timeout=30) as response:
        raw = response.read().decode("utf-8", errors="replace")
        return json.loads(raw)


def iter_fuel_records(node):
    if isinstance(node, dict):
        product = node.get("product")
        rest = node.get("rest")

        if isinstance(product, dict) and isinstance(rest, dict):
            yield node

        for value in node.values():
            yield from iter_fuel_records(value)

    elif isinstance(node, list):
        for item in node:
            yield from iter_fuel_records(item)


def fuel_record_id(record):
    candidates = [
        record.get("id"),
        record.get("oilProductId"),
        record.get("oil_product_id"),
    ]

    product = record.get("product") or {}

    candidates.extend(
        [
            product.get("emisId"),
            product.get("id"),
            product.get("productId"),
        ]
    )

    for value in candidates:
        if value is not None:
            return str(value)

    return ""


def delivery_is_positive(value):
    if isinstance(value, bool):
        return value

    if isinstance(value, (int, float)):
        return value != 0

    text = str(value or "").strip().lower()

    negative_values = {
        "",
        "0",
        "no",
        "false",
        "none",
        "null",
        "off",
        "нет",
        "n",
    }

    return text not in negative_values


def build_fuel_status(record):
    if record is None:
        return {
            "status": "НЕ ПРОДАЁТСЯ / НЕТ ДАННЫХ",
            "available": False,
            "in_transit": False,
            "delivery_raw": None,
            "since": None,
            "price": None,
        }

    rest = record.get("rest") or {}
    price_block = record.get("price") or {}

    available = bool(rest.get("avail"))
    delivery_raw = rest.get("delivery")
    in_transit = delivery_is_positive(delivery_raw)

    if available:
        status = "ЕСТЬ"
    elif in_transit:
        status = "В ПУТИ"
    else:
        status = "НЕТ"

    return {
        "status": status,
        "available": available,
        "in_transit": in_transit,
        "delivery_raw": delivery_raw,
        "since": rest.get("since"),
        "price": price_block.get("price"),
        "currency": price_block.get("currency"),
    }


def find_target_stations(stations):
    result = []

    for station in stations:
        number = normalize_station_number(
            station.get("PNPONumber")
        )

        city = str(station.get("city") or "")
        address = str(station.get("address") or "")
        name = str(station.get("name") or "")

        haystack = f"{city} {address} {name}".lower()

        if (
            number in TARGET_NUMBERS
            and TARGET_CITY_TOKEN in haystack
        ):
            result.append(station)

    return result


def check_station(opener, station):
    number = normalize_station_number(
        station.get("PNPONumber")
    )

    gpnazsid = station.get("GPNAZSID")

    result = {
        "number": number.zfill(3),
        "gpnazsid": gpnazsid,
        "name": station.get("name"),
        "city": station.get("city"),
        "address": station.get("address"),
        "open": station.get("open"),
        "fuels": {},
    }

    if not gpnazsid:
        result["error"] = "У АЗС отсутствует GPNAZSID"
        return result

    try:
        detail = post_json(
            opener,
            f"{BASE}/api/stations/{gpnazsid}",
            {},
        )

        records = {}

        for record in iter_fuel_records(detail):
            rid = fuel_record_id(record)

            if rid and rid not in records:
                records[rid] = record

        for fuel_id, fuel_name in TRACKED_FUELS.items():
            result["fuels"][fuel_name] = build_fuel_status(
                records.get(fuel_id)
            )

    except Exception as error:
        result["error"] = (
            f"{type(error).__name__}: {error}"
        )

        for fuel_name in TRACKED_FUELS.values():
            result["fuels"][fuel_name] = {
                "status": "ОШИБКА",
                "available": False,
                "in_transit": False,
                "delivery_raw": None,
                "since": None,
                "price": None,
            }

    return result


def run_check():
    started = time.time()

    opener = make_opener()
    prime_session(opener)

    stations_data = post_json(
        opener,
        STATIONS_API,
        {},
    )

    all_stations = stations_data.get("stations", [])
    targets = find_target_stations(all_stations)

    checked = [
        check_station(opener, station)
        for station in targets
    ]

    checked.sort(key=lambda x: x["number"])

    alerts = []

    for station in checked:
        for fuel_name, fuel in station["fuels"].items():
            if fuel.get("status") == "В ПУТИ":
                alerts.append(
                    {
                        "station": station["number"],
                        "address": station.get("address"),
                        "fuel": fuel_name,
                        "status": "В ПУТИ",
                        "delivery_raw": fuel.get(
                            "delivery_raw"
                        ),
                        "since": fuel.get("since"),
                    }
                )

    found_numbers = {
        item["number"].lstrip("0") or "0"
        for item in checked
    }

    missing = sorted(
        TARGET_NUMBERS - found_numbers,
        key=lambda x: int(x),
    )

    return {
        "ok": True,
        "checked_at_utc": datetime.now(
            timezone.utc
        ).isoformat(),
        "elapsed_seconds": round(
            time.time() - started,
            2,
        ),
        "total_stations_received": len(all_stations),
        "target_stations_found": len(checked),
        "missing_target_numbers": [
            number.zfill(3)
            for number in missing
        ],
        "alerts": alerts,
        "stations": checked,
    }


@app.route("/")
def home():
    return jsonify(
        {
            "ok": True,
            "service": "gpn-fuel-monitor",
            "mode": "direct-gpn-api",
            "tracked_stations": [
                "062",
                "065",
                "066",
                "111",
            ],
            "tracked_fuels": list(
                TRACKED_FUELS.values()
            ),
            "endpoints": [
                "/check",
                "/probe",
            ],
        }
    )


@app.route("/check")
def check():
    try:
        return jsonify(run_check())

    except Exception as error:
        return (
            jsonify(
                {
                    "ok": False,
                    "error": type(error).__name__,
                    "message": str(error),
                }
            ),
            500,
        )


@app.route("/probe")
def probe():
    return check()


if __name__ == "__main__":
    port = int(
        os.environ.get(
            "PORT",
            "8080",
        )
    )

    app.run(
        host="0.0.0.0",
        port=port,
    )
