import json
import os
import time
import http.cookiejar
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from flask import Flask, jsonify, request


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

REQUEST_TIMEOUT = 10
MAX_ATTEMPTS = 3
CHECK_DEADLINE_SECONDS = 75

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

    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(cookies)
    )

    return opener


def sleep_before_retry(attempt):
    if attempt < MAX_ATTEMPTS:
        time.sleep(min(1.5 * attempt, 3))


def safe_error_text(error):
    message = str(error)

    if hasattr(error, "reason") and error.reason:
        message = f"{message}; reason={error.reason}"

    return message[:500]


def prime_session(opener, deadline):
    last_error = None

    for attempt in range(1, MAX_ATTEMPTS + 1):
        if time.monotonic() >= deadline:
            raise TimeoutError(
                "Общий лимит времени проверки исчерпан "
                "до установления сессии с GPN"
            )

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

        try:
            remaining = max(
                1,
                min(
                    REQUEST_TIMEOUT,
                    int(deadline - time.monotonic()),
                ),
            )

            with opener.open(
                req,
                timeout=remaining,
            ) as response:
                response.read(200000)

            return {
                "ok": True,
                "attempts": attempt,
            }

        except Exception as error:
            last_error = error
            sleep_before_retry(attempt)

    raise RuntimeError(
        "Не удалось открыть страницу GPN после "
        f"{MAX_ATTEMPTS} попыток: "
        f"{safe_error_text(last_error)}"
    )


def post_json(
    opener,
    url,
    payload,
    deadline,
):
    last_error = None

    for attempt in range(1, MAX_ATTEMPTS + 1):
        if time.monotonic() >= deadline:
            raise TimeoutError(
                "Общий лимит времени проверки исчерпан"
            )

        body = json.dumps(
            payload or {}
        ).encode("utf-8")

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

        try:
            remaining = max(
                1,
                min(
                    REQUEST_TIMEOUT,
                    int(deadline - time.monotonic()),
                ),
            )

            with opener.open(
                req,
                timeout=remaining,
            ) as response:
                raw = response.read().decode(
                    "utf-8",
                    errors="replace",
                )

            return {
                "ok": True,
                "attempts": attempt,
                "data": json.loads(raw),
            }

        except Exception as error:
            last_error = error
            sleep_before_retry(attempt)

    return {
        "ok": False,
        "attempts": MAX_ATTEMPTS,
        "error": type(last_error).__name__,
        "message": safe_error_text(last_error),
        "data": None,
    }


def iter_fuel_records(node):
    if isinstance(node, dict):
        product = node.get("product")
        rest = node.get("rest")

        if (
            isinstance(product, dict)
            and isinstance(rest, dict)
        ):
            yield node

        for value in node.values():
            yield from iter_fuel_records(value)

    elif isinstance(node, list):
        for item in node:
            yield from iter_fuel_records(item)


def fuel_record_id(record):
    product = record.get("product") or {}

    candidates = [
        record.get("id"),
        record.get("oilProductId"),
        record.get("oil_product_id"),
        product.get("emisId"),
        product.get("id"),
        product.get("productId"),
    ]

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
            "currency": None,
        }

    rest = record.get("rest") or {}
    price_block = record.get("price") or {}

    available = bool(rest.get("avail"))
    delivery_raw = rest.get("delivery")
    in_transit = delivery_is_positive(
        delivery_raw
    )

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

        city = str(
            station.get("city") or ""
        )
        address = str(
            station.get("address") or ""
        )
        name = str(
            station.get("name") or ""
        )

        haystack = (
            f"{city} {address} {name}"
        ).lower()

        if (
            number in TARGET_NUMBERS
            and TARGET_CITY_TOKEN in haystack
        ):
            result.append(station)

    return result


def blank_fuels(status):
    return {
        fuel_name: {
            "status": status,
            "available": False,
            "in_transit": False,
            "delivery_raw": None,
            "since": None,
            "price": None,
            "currency": None,
        }
        for fuel_name in TRACKED_FUELS.values()
    }


def check_station(
    opener,
    station,
    deadline,
):
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
        result["error"] = (
            "У АЗС отсутствует GPNAZSID"
        )
        result["fuels"] = blank_fuels(
            "ОШИБКА"
        )
        return result

    response = post_json(
        opener,
        f"{BASE}/api/stations/{gpnazsid}",
        {},
        deadline,
    )

    result["api_attempts"] = response[
        "attempts"
    ]

    if not response["ok"]:
        result["error"] = (
            f'{response["error"]}: '
            f'{response["message"]}'
        )
        result["fuels"] = blank_fuels(
            "ОШИБКА"
        )
        return result

    detail = response["data"]

    records = {}

    for record in iter_fuel_records(
        detail
    ):
        record_id = fuel_record_id(
            record
        )

        if (
            record_id
            and record_id not in records
        ):
            records[record_id] = record

    for fuel_id, fuel_name in (
        TRACKED_FUELS.items()
    ):
        result["fuels"][
            fuel_name
        ] = build_fuel_status(
            records.get(fuel_id)
        )

    return result


def run_check():
    started = time.monotonic()

    deadline = (
        started + CHECK_DEADLINE_SECONDS
    )

    opener = make_opener()

    prime_info = prime_session(
        opener,
        deadline,
    )

    station_list_response = post_json(
        opener,
        STATIONS_API,
        {},
        deadline,
    )

    if not station_list_response["ok"]:
        return {
            "ok": False,
            "stage": "stations_list",
            "error": station_list_response[
                "error"
            ],
            "message": station_list_response[
                "message"
            ],
            "attempts": station_list_response[
                "attempts"
            ],
            "elapsed_seconds": round(
                time.monotonic() - started,
                2,
            ),
        }

    stations_data = station_list_response[
        "data"
    ]

    all_stations = stations_data.get(
        "stations",
        [],
    )

    targets = find_target_stations(
        all_stations
    )

    checked = []

    with ThreadPoolExecutor(
        max_workers=4
    ) as executor:
        future_map = {
            executor.submit(
                check_station,
                opener,
                station,
                deadline,
            ): station
            for station in targets
        }

        for future in as_completed(
            future_map
        ):
            try:
                checked.append(
                    future.result()
                )

            except Exception as error:
                station = future_map[
                    future
                ]

                number = normalize_station_number(
                    station.get(
                        "PNPONumber"
                    )
                )

                checked.append(
                    {
                        "number": number.zfill(
                            3
                        ),
                        "gpnazsid": station.get(
                            "GPNAZSID"
                        ),
                        "name": station.get(
                            "name"
                        ),
                        "city": station.get(
                            "city"
                        ),
                        "address": station.get(
                            "address"
                        ),
                        "open": station.get(
                            "open"
                        ),
                        "error": (
                            f"{type(error).__name__}: "
                            f"{error}"
                        ),
                        "fuels": blank_fuels(
                            "ОШИБКА"
                        ),
                    }
                )

    checked.sort(
        key=lambda item: item["number"]
    )

    alerts = []

    for station in checked:
        for fuel_name, fuel in (
            station["fuels"].items()
        ):
            if (
                fuel.get("status")
                == "В ПУТИ"
            ):
                alerts.append(
                    {
                        "station": station[
                            "number"
                        ],
                        "address": station.get(
                            "address"
                        ),
                        "fuel": fuel_name,
                        "status": "В ПУТИ",
                        "delivery_raw": fuel.get(
                            "delivery_raw"
                        ),
                        "since": fuel.get(
                            "since"
                        ),
                    }
                )

    found_numbers = {
        item["number"].lstrip("0")
        or "0"
        for item in checked
    }

    missing = sorted(
        TARGET_NUMBERS
        - found_numbers,
        key=lambda x: int(x),
    )

    return {
        "ok": True,
        "checked_at_utc": datetime.now(
            timezone.utc
        ).isoformat(),
        "elapsed_seconds": round(
            time.monotonic() - started,
            2,
        ),
        "request_timeout_seconds": (
            REQUEST_TIMEOUT
        ),
        "max_attempts": MAX_ATTEMPTS,
        "session_attempts": (
            prime_info["attempts"]
        ),
        "stations_list_attempts": (
            station_list_response[
                "attempts"
            ]
        ),
        "total_stations_received": len(
            all_stations
        ),
        "target_stations_found": len(
            checked
        ),
        "missing_target_numbers": [
            number.zfill(3)
            for number in missing
        ],
        "alerts": alerts,
        "stations": checked,
    }


@app.route("/", methods=["GET", "POST"])
def home():
    if request.method == "POST":
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

    return jsonify(
        {
            "ok": True,
            "service": "gpn-fuel-monitor",
            "mode": "direct-gpn-api-retry",
            "tracked_stations": [
                "062",
                "065",
                "066",
                "111",
            ],
            "tracked_fuels": list(
                TRACKED_FUELS.values()
            ),
            "request_timeout_seconds": (
                REQUEST_TIMEOUT
            ),
            "max_attempts": (
                MAX_ATTEMPTS
            ),
            "endpoints": [
                "/",
                "/check",
                "/probe",
            ],
            "timer_ready": True,
        }
    )


@app.route("/check")
def check():
    try:
        return jsonify(
            run_check()
        )

    except Exception as error:
        return (
            jsonify(
                {
                    "ok": False,
                    "error": type(
                        error
                    ).__name__,
                    "message": str(
                        error
                    ),
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
