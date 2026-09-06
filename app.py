import os
import time
from flask import Flask, jsonify
from playwright.sync_api import sync_playwright

app = Flask(__name__)

PAGE = "https://gpnbonus.ru/fuel/refuel-map"

TARGETS = [
    {"number": "062", "address": "Ленина, 27"},
    {"number": "066", "address": "Ленина, 4"},
    {"number": "065", "address": "Кадочникова, 4-й км"},
    {"number": "111", "address": "Суворова, 12"},
]

FUELS = ["АИ-95", "G-95", "G-100"]
STATUS = "В пути"

KEYWORDS = [
    "api",
    "fuel",
    "station",
    "refuel",
    "azs",
    "map",
    "graphql",
    "ajax",
    "petrol",
    "availability",
    "status",
]


def has_keyword(text):
    text = (text or "").lower()
    return any(word in text for word in KEYWORDS)


def run_probe():
    started = time.time()
    captured = []

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )

        context = browser.new_context(
            locale="ru-RU",
            timezone_id="Asia/Yekaterinburg",
            geolocation={
                "latitude": 56.4145,
                "longitude": 61.9180,
            },
            permissions=["geolocation"],
        )

        page = context.new_page()

        def handle_response(response):
            try:
                content_type = (
                    response.headers.get("content-type") or ""
                ).lower()

                if "json" in content_type or has_keyword(response.url):
                    item = {
                        "url": response.url,
                        "status": response.status,
                        "content_type": content_type,
                    }

                    if "json" in content_type:
                        try:
                            item["sample"] = response.text()[:2000]
                        except Exception:
                            pass

                    captured.append(item)

            except Exception:
                pass

        page.on("response", handle_response)

        navigation_error = None

        try:
            page.goto(
                PAGE,
                wait_until="domcontentloaded",
                timeout=30000,
            )
        except Exception as e:
            navigation_error = str(e)

        # Даём JavaScript карты время загрузить данные АЗС
        page.wait_for_timeout(10000)

        try:
            body_text = page.locator("body").inner_text(timeout=5000)
        except Exception:
            body_text = ""

        try:
            title = page.title()
        except Exception:
            title = ""

        browser.close()

    stations = []

    for station in TARGETS:
        number = station["number"]
        number_without_zero = number.lstrip("0")
        address = station["address"]

        stations.append(
            {
                "number": number,
                "address": address,
                "number_found": (
                    number in body_text
                    or number_without_zero in body_text
                ),
                "address_found": address.lower() in body_text.lower(),
            }
        )

    fuels = {
        fuel: fuel.lower() in body_text.lower()
        for fuel in FUELS
    }

    status_found = STATUS.lower() in body_text.lower()

    # Убираем повторяющиеся сетевые запросы
    unique_responses = []
    seen_urls = set()

    for item in captured:
        url = item.get("url")

        if url in seen_urls:
            continue

        seen_urls.add(url)
        unique_responses.append(item)

    relevant_responses = []

    for item in unique_responses:
        combined = (
            (item.get("url") or "")
            + " "
            + (item.get("sample") or "")
        )

        if (
            has_keyword(combined)
            or any(
                target["address"].lower() in combined.lower()
                for target in TARGETS
            )
            or any(
                fuel.lower() in combined.lower()
                for fuel in FUELS
            )
            or STATUS.lower() in combined.lower()
        ):
            relevant_responses.append(item)

    return {
        "ok": True,
        "elapsed_seconds": round(time.time() - started, 2),
        "page_title": title,
        "navigation_error": navigation_error,
        "body_text_size": len(body_text),
        "stations": stations,
        "fuels": fuels,
        "status_in_transit_found": status_found,
        "captured_response_count": len(unique_responses),
        "relevant_responses": relevant_responses[:30],
        "body_sample": body_text[:1500],
    }


@app.route("/")
def home():
    return jsonify(
        {
            "ok": True,
            "service": "gpn-fuel-monitor",
            "message": "Container is running",
        }
    )


@app.route("/probe")
def probe():
    try:
        return jsonify(run_probe())

    except Exception as e:
        return (
            jsonify(
                {
                    "ok": False,
                    "error": type(e).__name__,
                    "message": str(e),
                }
            ),
            500,
        )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))

    app.run(
        host="0.0.0.0",
        port=port,
    )
