import html
import os
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from flask import Flask, jsonify


app = Flask(__name__)

PAGE = "https://gpnbonus.ru/fuel/refuel-map"

TARGETS = [
    {"number": "062", "address": "Ленина, 27"},
    {"number": "066", "address": "Ленина, 4"},
    {"number": "065", "address": "Кадочникова, 4-й км"},
    {"number": "111", "address": "Суворова, 12"},
]

FUELS = [
    "АИ-95",
    "G-95",
    "G-100",
]

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


def contains_keyword(text):
    value = (text or "").lower()
    return any(keyword in value for keyword in KEYWORDS)


def clean_text(value):
    value = html.unescape(value or "")
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def fetch_page():
    print("HTTP PROBE 1: starting request", flush=True)

    request = Request(
        PAGE,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/152.0 Safari/537.36"
            ),
            "Accept": (
                "text/html,application/xhtml+xml,"
                "application/xml;q=0.9,image/avif,"
                "image/webp,*/*;q=0.8"
            ),
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
        },
        method="GET",
    )

    try:
        with urlopen(request, timeout=30) as response:
            print("HTTP PROBE 2: response received", flush=True)

            raw = response.read()

            print(
                f"HTTP PROBE 3: downloaded {len(raw)} bytes",
                flush=True,
            )

            charset = response.headers.get_content_charset()

            if not charset:
                charset = "utf-8"

            text = raw.decode(
                charset,
                errors="replace",
            )

            return {
                "ok": True,
                "status": response.status,
                "final_url": response.geturl(),
                "content_type": response.headers.get(
                    "Content-Type",
                    "",
                ),
                "text": text,
            }

    except HTTPError as error:
        try:
            body = error.read().decode(
                "utf-8",
                errors="replace",
            )
        except Exception:
            body = ""

        return {
            "ok": False,
            "status": error.code,
            "error": "HTTPError",
            "message": str(error),
            "text": body,
        }

    except URLError as error:
        return {
            "ok": False,
            "status": None,
            "error": "URLError",
            "message": str(error),
            "text": "",
        }

    except Exception as error:
        return {
            "ok": False,
            "status": None,
            "error": type(error).__name__,
            "message": str(error),
            "text": "",
        }


def extract_title(page_text):
    match = re.search(
        r"<title[^>]*>(.*?)</title>",
        page_text,
        flags=re.IGNORECASE | re.DOTALL,
    )

    if not match:
        return ""

    return clean_text(match.group(1))


def extract_scripts(page_text):
    scripts = []

    pattern = re.compile(
        r"""<script[^>]+src=["']([^"']+)["']""",
        flags=re.IGNORECASE,
    )

    for src in pattern.findall(page_text):
        full_url = urljoin(PAGE, html.unescape(src))

        if full_url not in scripts:
            scripts.append(full_url)

    return scripts


def extract_urls(page_text):
    found = []

    absolute_pattern = re.compile(
        r"""https?://[^\s"'<>\\]+""",
        flags=re.IGNORECASE,
    )

    for url in absolute_pattern.findall(page_text):
        url = html.unescape(url)

        if contains_keyword(url):
            if url not in found:
                found.append(url)

    path_pattern = re.compile(
        r"""["']([^"']*(?:api|graphql|fuel|refuel|"""
        r"""station|azs|availability|status)[^"']*)["']""",
        flags=re.IGNORECASE,
    )

    for value in path_pattern.findall(page_text):
        value = html.unescape(value)

        if len(value) > 300:
            continue

        if value.startswith("/"):
            value = urljoin(PAGE, value)

        if value not in found:
            found.append(value)

    return found


def find_keyword_snippets(page_text):
    snippets = []

    lowered = page_text.lower()

    search_values = (
        [target["address"] for target in TARGETS]
        + [target["number"] for target in TARGETS]
        + FUELS
        + [STATUS]
    )

    for value in search_values:
        pos = lowered.find(value.lower())

        if pos == -1:
            continue

        start = max(0, pos - 200)
        end = min(len(page_text), pos + 500)

        snippet = clean_text(
            page_text[start:end]
        )

        snippets.append(
            {
                "search": value,
                "snippet": snippet,
            }
        )

    return snippets[:20]


def run_probe():
    started = time.time()

    result = fetch_page()

    page_text = result.get("text", "")

    if not result.get("ok"):
        return {
            "ok": False,
            "elapsed_seconds": round(
                time.time() - started,
                2,
            ),
            "http_status": result.get("status"),
            "error": result.get("error"),
            "message": result.get("message"),
            "body_sample": page_text[:1500],
        }

    print("HTTP PROBE 4: analysing HTML", flush=True)

    title = extract_title(page_text)
    scripts = extract_scripts(page_text)
    candidate_urls = extract_urls(page_text)

    stations = []

    lowered = page_text.lower()

    for station in TARGETS:
        number = station["number"]
        number_without_zero = number.lstrip("0")
        address = station["address"]

        stations.append(
            {
                "number": number,
                "address": address,
                "number_found": (
                    number.lower() in lowered
                    or (
                        number_without_zero
                        and number_without_zero.lower()
                        in lowered
                    )
                ),
                "address_found": (
                    address.lower() in lowered
                ),
            }
        )

    fuels = {
        fuel: fuel.lower() in lowered
        for fuel in FUELS
    }

    status_found = STATUS.lower() in lowered

    snippets = find_keyword_snippets(page_text)

    print("HTTP PROBE 5: completed", flush=True)

    return {
        "ok": True,
        "elapsed_seconds": round(
            time.time() - started,
            2,
        ),
        "http_status": result.get("status"),
        "final_url": result.get("final_url"),
        "content_type": result.get(
            "content_type",
            "",
        ),
        "page_title": title,
        "html_size": len(page_text),
        "stations": stations,
        "fuels": fuels,
        "status_in_transit_found": status_found,
        "script_count": len(scripts),
        "scripts": scripts[:30],
        "candidate_url_count": len(candidate_urls),
        "candidate_urls": candidate_urls[:50],
        "keyword_snippets": snippets,
        "body_sample": clean_text(
            page_text[:2000]
        ),
    }


@app.route("/")
def home():
    return jsonify(
        {
            "ok": True,
            "service": "gpn-fuel-monitor",
            "message": "Container is running",
            "mode": "direct-http",
        }
    )


@app.route("/probe")
def probe():
    try:
        return jsonify(run_probe())

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
