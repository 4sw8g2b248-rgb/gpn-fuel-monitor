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
    print("PROBE 1: started", flush=True)

    started = time.time()
    captured = []
    navigation_error = None
    body_text = ""
    title = ""

    print("PROBE 2: before sync_playwright", flush=True)

    with sync_playwright() as p:
        print("PROBE 3: playwright started", flush=True)

        browser = p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )

        print("PROBE 4: browser launched", flush=True)

        context = browser.new_context(
            locale="ru-RU",
            timezone_id="Asia/Yekaterinburg",
            geolocation={
                "latitude": 56.4145,
                "longitude": 61.9180,
            },
            permissions=["geolocation"],
        )

        print("PROBE 5: context created", flush=True)

        page = context.new_page()

        print("PROBE 6: page created", flush=True)

        def handle_response(response):
            try:
                content_type = (
                    response.headers.get("content-type") or ""
                ).lower()

                if "json" in content_type or has_keyword(response.url):
                    captured.append(
                        {
                            "url": response.url,
                            "status": response.status,
                            "content_type": content_type,
                        }
                    )
            except Exception:
                pass

        page.on("response", handle_response)

        try:
            print("PROBE 7: before goto", flush=True)

            try:
                page.goto(
                    PAGE,
                    wait_until="domcontentloaded",
                    timeout=30000,
                )

                print("PROBE 8: goto finished", flush=True)

            except Exception as e:
                navigation_error = str(e)

                print(
                    f"PROBE 8A: goto error: {type(e).__name__}: {e}",
                    flush=True,
                )

            print("PROBE 9: before wait", flush=True)

            page.wait_for_timeout(10000)

            print("PROBE 10: wait finished", flush=True)

            try:
                print("PROBE 11: before body text", flush=True)

                body_text = page.locator("body").inner_text(
                    timeout=5000
                )

                print("PROBE 12: body text received", flush=True)

            except Exception as e:
                body_text = ""

                print(
                    f"PROBE 12A: body error: "
                    f"{type(e).__name__}: {e}",
                    flush=True,
                )

            try:
                print("PROBE 13: before title", flush=True)

                title = page.title()

                print("PROBE 14: title received", flush=True)

            except Exception as e:
                title = ""

                print(
                    f"PROBE 14A: title error: "
                    f"{type(e).__name__}: {e}",
                    flush=True,
                )

        finally:
            print(
                "PROBE 15: before remove listener",
                flush=True,
            )

            try:
                page.remove_listener(
                    "response",
                    handle_response,
                )

            except Exception as e:
                print(
                    f"PROBE 15A: listener error: "
                    f"{type(e).__name__}: {e}",
                    flush=True,
                )

            print(
                "PROBE 16: before browser close",
                flush=True,
            )

            browser.close()

            print(
                "PROBE 17: browser closed",
                flush=True,
            )

    print(
        "PROBE 18: playwright block finished",
        flush=True,
    )

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
                "address_found": (
                    address.lower()
                    in body_text.lower()
                ),
            }
        )

    fuels = {
        fuel: fuel.lower() in body_text.lower()
        for fuel in FUELS
    }

    status_found = (
        STATUS.lower() in body_text.lower()
    )

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
        combined = item.get("url") or ""

        if (
            has_keyword(combined)
            or any(
                target["address"].lower()
                in combined.lower()
                for target in TARGETS
            )
            or any(
                fuel.lower()
                in combined.lower()
                for fuel in FUELS
            )
            or STATUS.lower()
            in combined.lower()
        ):
            relevant_responses.append(item)

    print(
        "PROBE 19: completed successfully",
        flush=True,
    )

    return {
        "ok": True,
        "elapsed_seconds": round(
            time.time() - started,
            2,
        ),
        "page_title": title,
        "navigation_error": navigation_error,
        "body_text_size": len(body_text),
        "stations": stations,
        "fuels": fuels,
        "status_in_transit_found": status_found,
        "captured_response_count": len(
            unique_responses
        ),
        "relevant_responses": (
            relevant_responses[:30]
        ),
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
    port = int(
        os.environ.get("PORT", "8080")
    )

    app.run(
        host="0.0.0.0",
        port=port,
    )
