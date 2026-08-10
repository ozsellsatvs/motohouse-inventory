#!/usr/bin/env python3
"""
Scrapes the public inventory listing at motohousems.com (a Dealer Spike
powered powersports dealer site) and writes a structured JSON file
(data/inventory.json) that the front-end app reads.

The site server-renders its inventory list (no JS execution required),
so a plain requests + BeautifulSoup parse works.

Design notes / how this is built:
  - Each vehicle "Get a Quote" link on the list page carries the vehicle's
    core facts as literal query-string parameters (oid, condition, year,
    make, model, vtype, trimid, stockno, vin). We parse those directly --
    it's the most reliable signal on the page, independent of styling.
  - Extra fields not present in that query string (Category subtype,
    Color, Availability, Odometer) live in a "Quick Look" panel per
    vehicle, rendered as a list of <h5>Label</h5>Value items. We find
    those by label text, not by CSS class (class names are not stable
    across Dealer Spike sites/updates).
  - Price ("Our Price" / "Retail Price" / "Savings") is pulled from the
    "Limited Time Offer" quote link text near each vehicle.
  - A vehicle detail page's "Manufacturer Info" accordion tab
    (id="accordionManufacturerInfo") holds the full spec sheet -- Engine,
    Drivetrain, Suspension, Brakes, Wheels & Tires, Dimensions,
    Capacities, Weights, Color -- one <li class="liUnit ... unitSpec ...">
    per row, with the label in a <label class="unitLabel"> and the value
    in a <span class="unitValue"> (section-header rows like "Engine" have
    no value span). We keep this as structured data -- a list of
    {"section": "Engine", "items": [{"label": ..., "value": ...}, ...]}
    dicts, stored in the "specs" field -- rather than flattening it to a
    text blob, so the front-end can render each spec as its own line
    instead of a run-together paragraph. If a listing has no Manufacturer
    Info panel, we fall back to the page's og:description meta tag in the
    plain "description" field.
  - This runs on a self-hosted GitHub Actions runner (the dealer's own
    home internet connection), not GitHub's cloud servers, because
    motohousems.com's firewall blocks cloud/datacenter IP ranges. See the
    repo README for details -- no proxy service is needed.

Usage:
    python scrape.py --out ../docs/data/inventory.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse, parse_qs

import requests
from bs4 import BeautifulSoup

BASE = "https://www.motohousems.com"
LIST_PATH = "/--inventory"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
EXTRA_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
REQUEST_TIMEOUT = 60
PAGE_DELAY_SEC = 0.6          # be polite between list-page requests
DETAIL_DELAY_SEC = 0.35       # be polite between detail-page requests
CONDITIONS = ["new", "pre-owned"]

# Bumped whenever the shape of what we store per-vehicle changes in a way
# that makes previously-cached detail-page data (image_url/description/
# specs) stale or incomplete. load_detail_cache() ignores a previous run's
# data entirely if its schema doesn't match, forcing one full re-fetch
# instead of silently keeping old-format data around.
SCHEMA_VERSION = 6

QUICKLOOK_LABELS = [
    "Condition",
    "Availability",
    "Stock Number",
    "Vin",
    "Vehicle Type",
    "Category",
    "Odometer",
    "Mileage",
    "Color",
    "Fuel Type",
    "Engine",
]
# longest-first so "Stock Number" matches before a hypothetical "Stock"
QUICKLOOK_LABELS.sort(key=len, reverse=True)


@dataclass
class Vehicle:
    id: str
    year: int | None
    make: str
    model: str
    title: str
    condition: str
    availability: str | None = None
    vehicle_type: str | None = None
    category: str | None = None
    group_label: str | None = None
    color: str | None = None
    odometer: int | None = None
    stock_number: str | None = None
    vin: str | None = None
    price: int | None = None
    retail_price: int | None = None
    savings: int | None = None
    detail_url: str | None = None
    image_url: str | None = None
    description: str | None = None
    specs: list[dict] | None = None


def fetch(url: str, session: requests.Session) -> str | None:
    try:
        headers = {"User-Agent": USER_AGENT, **EXTRA_HEADERS}
        resp = session.get(url, timeout=REQUEST_TIMEOUT, headers=headers)
        resp.raise_for_status()
        return resp.text
    except requests.RequestException as exc:
        print(f"  ! failed to fetch {url}: {exc}", file=sys.stderr)
        return None


def parse_quote_links(soup: BeautifulSoup) -> dict[str, dict]:
    """Find every 'Get A Quote' style link and pull the vehicle facts out
    of its query string. Returns a dict keyed by oid (dedup across the
    multiple links -- quote/contact/trade/print -- that repeat per vehicle)."""
    vehicles: dict[str, dict] = {}
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "xt-xInquiry" not in href and "xInventoryDetail" not in href:
            continue
        qs = parse_qs(urlparse(href).query)
        oid = qs.get("oid", [None])[0]
        if not oid or oid in vehicles:
            continue
        def one(key):
            v = qs.get(key, [None])[0]
            return v
        vehicles[oid] = {
            "oid": oid,
            "condition": (one("condition") or "").upper(),
            "year": one("year"),
            "make": one("make"),
            "model": one("model"),
            "vtype": one("vtype"),
            "trimid": one("trimid"),
            "stockno": one("stockno"),
            "vin": one("vin"),
        }
    return vehicles


def find_detail_url(soup: BeautifulSoup, oid: str) -> str | None:
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.rstrip("/").split("?")[0].endswith(f"-{oid}") and (
            href.startswith("/NEW-Inventory") or href.startswith("/USED-Inventory") or
            "/NEW-Inventory" in href or "/USED-Inventory" in href
        ):
            return urljoin(BASE, href.split("?")[0])
    return None


def find_quicklook_fields(soup: BeautifulSoup) -> list[dict]:
    """Each vehicle has a 'Quick Look' panel: a run of <li> elements where
    each li = <h5>Label</h5>Value. We return one dict of fields per panel,
    in document order (same order the vehicles appear on the page)."""
    panels = []
    for h4 in soup.find_all(["h4", "h3"]):
        if "quick look" not in h4.get_text(strip=True).lower():
            continue
        # the panel is generally the closest ancestor container; walk up
        # until we find one that contains <li> elements with <h5> labels
        container = h4.find_parent()
        lis = []
        node = container
        depth = 0
        while node is not None and depth < 6:
            lis = node.find_all("li")
            if any(li.find("h5") for li in lis):
                break
            node = node.find_parent()
            depth += 1
        fields = {}
        for li in lis:
            h5 = li.find("h5")
            if not h5:
                continue
            label = h5.get_text(strip=True)
            match = next((lbl for lbl in QUICKLOOK_LABELS if label.startswith(lbl)), None)
            if not match:
                continue
            value = li.get_text(" ", strip=True)
            value = value[len(match):].strip() if value.startswith(match) else value
            fields[match] = value
        if fields:
            panels.append(fields)
    return panels


RETAIL_PRICE_RE = re.compile(r"Retail Price\s*\$(?P<retail>[\d,]+)", re.IGNORECASE)
OUR_PRICE_RE = re.compile(r"Our Price\s*\$(?P<price>[\d,]+)", re.IGNORECASE)
SAVINGS_RE = re.compile(r"Savings\s*\$(?P<savings>[\d,]+)", re.IGNORECASE)


def find_prices(soup: BeautifulSoup) -> list[dict]:
    """Prices appear in the 'Limited Time Offer! ...' quote link text, in
    the same order vehicles appear on the page.

    Each field (retail price / our price / savings) is searched for
    independently rather than as one combined regex. A combined regex
    where every piece is wrapped as optional will happily match an empty
    string at the very start of the text -- before ever reaching the
    actual numbers -- so `.search()` returns a match object where every
    group is silently None. Searching separately avoids that trap."""
    prices = []
    for a in soup.find_all("a", href=True):
        text = a.get_text(" ", strip=True)
        if "Our Price" not in text and "Retail Price" not in text:
            continue
        def num(pattern, key):
            m = pattern.search(text)
            return int(m.group(key).replace(",", "")) if m else None
        prices.append({
            "retail_price": num(RETAIL_PRICE_RE, "retail"),
            "price": num(OUR_PRICE_RE, "price"),
            "savings": num(SAVINGS_RE, "savings"),
        })
    return prices


def find_manufacturer_info(soup: BeautifulSoup) -> list[dict] | None:
    """The vehicle detail page has a 'Manufacturer Info' accordion tab,
    id="accordionManufacturerInfo" -- the full spec sheet (Engine,
    Drivetrain, Suspension, Brakes, Wheels & Tires, Dimensions,
    Capacities, Weights, Color, etc.). Each row is
    <li class="liUnit ... unitSpec ..."><label class="unitLabel
    lblUnitLabel">Label</label><span class="unitValue spnUnitValue">
    Value</span></li>. Section-header rows (e.g. "Engine") carry an extra
    "unitSpecHeader" class and have no value span -- we use those to
    group the actual spec lines under a heading.

    Returns structured data instead of a flattened text blob, so the
    front-end can render each spec on its own line:
        [{"section": "Engine", "items": [{"label": "Displacement",
          "value": "998cc"}, ...]}, ...]
    """
    container = soup.find(id="accordionManufacturerInfo")
    if not container:
        return None
    sections: list[dict] = []
    current: dict | None = None
    for li in container.find_all("li", class_="liUnit"):
        label_el = li.find("label", class_="unitLabel")
        if not label_el:
            continue
        label = label_el.get_text(strip=True)
        value_el = li.find("span", class_="unitValue")
        if not value_el:
            # section header row (e.g. "Engine", "Drivetrain") -- start a
            # new group that the following rows get filed under
            current = {"section": label, "items": []}
            sections.append(current)
        else:
            value = value_el.get_text(strip=True)
            if not value:
                continue
            if current is None:
                current = {"section": None, "items": []}
                sections.append(current)
            current["items"].append({"label": label, "value": value})
    sections = [s for s in sections if s["items"]]
    return sections or None


def clean_int(v):
    if v is None:
        return None
    v = re.sub(r"[^\d]", "", str(v))
    return int(v) if v else None


def scrape_list_page(url: str, session: requests.Session) -> tuple[list[Vehicle], int]:
    html = fetch(url, session)
    if not html:
        return [], 0
    soup = BeautifulSoup(html, "html.parser")

    m = re.search(r"Page\s+\d+\s+of\s+(\d+)", soup.get_text(" "))
    total_pages = int(m.group(1)) if m else 1

    quote_links = parse_quote_links(soup)  # dict keyed by oid, insertion order preserved (py3.7+)
    quicklook_panels = find_quicklook_fields(soup)
    prices = find_prices(soup)

    vehicles: list[Vehicle] = []
    for idx, (oid, facts) in enumerate(quote_links.items()):
        ql = quicklook_panels[idx] if idx < len(quicklook_panels) else {}
        pr = prices[idx] if idx < len(prices) else {}

        year = clean_int(facts.get("year"))
        make = (facts.get("make") or "").strip()
        model = (facts.get("model") or "").strip()
        title = " ".join(str(x) for x in [year, make, model] if x)

        vehicle_type = ql.get("Vehicle Type") or facts.get("vtype")
        category = ql.get("Category")
        group_label = category if (category and vehicle_type and category.lower() not in vehicle_type.lower()) else None
        if group_label:
            group_label = f"{category} {vehicle_type}"
        else:
            group_label = vehicle_type or "Other"

        vehicles.append(
            Vehicle(
                id=oid,
                year=year,
                make=make,
                model=model,
                title=title,
                condition=(ql.get("Condition") or facts.get("condition") or "").upper(),
                availability=ql.get("Availability"),
                vehicle_type=vehicle_type,
                category=category,
                group_label=group_label,
                color=ql.get("Color"),
                odometer=clean_int(ql.get("Odometer") or ql.get("Mileage")),
                stock_number=ql.get("Stock Number") or facts.get("stockno"),
                vin=ql.get("Vin") or facts.get("vin"),
                price=pr.get("price"),
                retail_price=pr.get("retail_price"),
                savings=pr.get("savings"),
                detail_url=find_detail_url(soup, oid),
            )
        )
    return vehicles, total_pages


def enrich_with_detail_page(v: Vehicle, session: requests.Session) -> None:
    if not v.detail_url:
        return
    html = fetch(v.detail_url, session)
    if not html:
        return
    soup = BeautifulSoup(html, "html.parser")
    og_image = soup.find("meta", property="og:image")
    if og_image and og_image.get("content"):
        v.image_url = og_image["content"]
    v.specs = find_manufacturer_info(soup)
    if not v.specs:
        og_desc = soup.find("meta", property="og:description") or soup.find("meta", attrs={"name": "description"})
        if og_desc and og_desc.get("content"):
            v.description = og_desc["content"].strip()


def load_detail_cache(path: str) -> dict[str, dict]:
    """Reuse image_url/description from the previous run so we don't spend
    time re-fetching the detail page of every vehicle every day -- only
    genuinely new listings need a fresh detail-page fetch. Ignored
    entirely (forcing a full re-fetch) if the previous run used a
    different SCHEMA_VERSION, since its cached fields may be in the old
    shape."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if data.get("schema") != SCHEMA_VERSION:
            return {}
        return {v["id"]: v for v in data.get("vehicles", []) if v.get("id")}
    except (OSError, json.JSONDecodeError, KeyError):
        return {}


def scrape_all(
    fetch_images: bool = True,
    max_pages: int | None = None,
    detail_cache: dict[str, dict] | None = None,
) -> list[Vehicle]:
    session = requests.Session()
    all_vehicles: dict[str, Vehicle] = {}

    for condition in CONDITIONS:
        page = 1
        total_pages = 1
        while page <= total_pages:
            if max_pages and page > max_pages:
                break
            url = f"{BASE}{LIST_PATH}?condition={condition}&pg={page}"
            print(f"Fetching {url}")
            vehicles, total_pages = scrape_list_page(url, session)
            for v in vehicles:
                all_vehicles[v.id] = v
            print(f"  -> {len(vehicles)} vehicles (page {page} of {total_pages})")
            page += 1
            time.sleep(PAGE_DELAY_SEC)

    vehicles = list(all_vehicles.values())

    if fetch_images:
        detail_cache = detail_cache or {}
        need_fetch = []
        for v in vehicles:
            cached = detail_cache.get(v.id)
            if cached and cached.get("image_url"):
                v.image_url = cached.get("image_url")
                v.description = cached.get("description")
                v.specs = cached.get("specs")
            else:
                need_fetch.append(v)
        print(
            f"Enriching {len(need_fetch)} of {len(vehicles)} vehicles with "
            f"photo/description from detail pages ({len(vehicles) - len(need_fetch)} "
            f"reused from yesterday's data)..."
        )
        for i, v in enumerate(need_fetch, 1):
            enrich_with_detail_page(v, session)
            if i % 25 == 0:
                print(f"  ...{i}/{len(need_fetch)}")
            time.sleep(DETAIL_DELAY_SEC)

    return vehicles


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="../docs/data/inventory.json")
    ap.add_argument("--no-images", action="store_true", help="skip per-vehicle detail page fetch (faster)")
    ap.add_argument("--max-pages", type=int, default=None, help="limit pages per condition (testing)")
    args = ap.parse_args()

    detail_cache = load_detail_cache(args.out)
    vehicles = scrape_all(
        fetch_images=not args.no_images,
        max_pages=args.max_pages,
        detail_cache=detail_cache,
    )

    if not vehicles and detail_cache:
        print(
            "No vehicles scraped (likely a temporary fetch failure) -- "
            "leaving the existing inventory.json in place instead of "
            "overwriting it with an empty result.",
            file=sys.stderr,
        )
        sys.exit(0)

    makes = sorted({v.make for v in vehicles if v.make})
    groups = sorted({v.group_label for v in vehicles if v.group_label})

    out = {
        "schema": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": f"{BASE}{LIST_PATH}",
        "count": len(vehicles),
        "makes": makes,
        "groups": groups,
        "vehicles": [asdict(v) for v in vehicles],
    }

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)

    print(f"Wrote {len(vehicles)} vehicles to {args.out}")


if __name__ == "__main__":
    main()
