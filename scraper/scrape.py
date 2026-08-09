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
  - A vehicle detail page's og:image / og:description meta tags are used
    for a photo + long description (best effort; a missing image just
    means the app falls back to a placeholder icon).

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
    "Mozilla/5.0 (compatible; MotohouseInventoryBot/1.0; "
    "+https://github.com/) requests"
)
REQUEST_TIMEOUT = 20
PAGE_DELAY_SEC = 0.6          # be polite between list-page requests
DETAIL_DELAY_SEC = 0.35       # be polite between detail-page requests
CONDITIONS = ["new", "pre-owned"]

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


def fetch(url: str, session: requests.Session) -> str | None:
    try:
        resp = session.get(url, timeout=REQUEST_TIMEOUT, headers={"User-Agent": USER_AGENT})
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


PRICE_RE = re.compile(
    r"(?:Retail Price\s*\$(?P<retail>[\d,]+))?\s*"
    r"(?:Our Price\s*\$(?P<price>[\d,]+))?\s*"
    r"(?:Savings\s*\$(?P<savings>[\d,]+))?",
    re.IGNORECASE,
)


def find_prices(soup: BeautifulSoup) -> list[dict]:
    """Prices appear in the 'Limited Time Offer! ...' quote link text, in
    the same order vehicles appear on the page."""
    prices = []
    for a in soup.find_all("a", href=True):
        text = a.get_text(" ", strip=True)
        if "Our Price" not in text and "Retail Price" not in text:
            continue
        m = PRICE_RE.search(text)
        if not m:
            continue
        def num(key):
            v = m.group(key)
            return int(v.replace(",", "")) if v else None
        prices.append({"retail_price": num("retail"), "price": num("price"), "savings": num("savings")})
    return prices


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
    og_desc = soup.find("meta", property="og:description") or soup.find("meta", attrs={"name": "description"})
    if og_desc and og_desc.get("content"):
        v.description = og_desc["content"].strip()


def scrape_all(fetch_images: bool = True, max_pages: int | None = None) -> list[Vehicle]:
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
        print(f"Enriching {len(vehicles)} vehicles with photo/description from detail pages...")
        for i, v in enumerate(vehicles, 1):
            enrich_with_detail_page(v, session)
            if i % 25 == 0:
                print(f"  ...{i}/{len(vehicles)}")
            time.sleep(DETAIL_DELAY_SEC)

    return vehicles


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="../docs/data/inventory.json")
    ap.add_argument("--no-images", action="store_true", help="skip per-vehicle detail page fetch (faster)")
    ap.add_argument("--max-pages", type=int, default=None, help="limit pages per condition (testing)")
    args = ap.parse_args()

    vehicles = scrape_all(fetch_images=not args.no_images, max_pages=args.max_pages)

    makes = sorted({v.make for v in vehicles if v.make})
    groups = sorted({v.group_label for v in vehicles if v.group_label})

    out = {
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
 