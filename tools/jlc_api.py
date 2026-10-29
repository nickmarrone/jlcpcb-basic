"""Fetch the live JLCPCB "basic" parts list.

Uses the same JSON endpoint as the jlcpcb.com parts search page. Records are
normalized to the fields the libraries care about, leaving out stock and price
so snapshots only change when a part really changes.
"""

import json
import time
import urllib.request

SEARCH_URL = "https://jlcpcb.com/api/overseas-pcb-order/v1/shoppingCart/smtGood/selectSmtComponentList"
FILE_URL = "https://jlcpcb.com/api/file/downloadByFileSystemAccessId/"
UA = {"User-Agent": "Mozilla/5.0 (jlc-basic-kicad-lib)", "Content-Type": "application/json"}


def _post(body, retries=4):
    req = urllib.request.Request(SEARCH_URL, json.dumps(body).encode(), UA)
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                data = json.load(r)
            if data.get("code") != 200:
                raise RuntimeError(f"JLCPCB API error: {data.get('message')}")
            return data["data"]["componentPageInfo"]
        except Exception:
            if attempt == retries - 1:
                raise
            time.sleep(5 * (attempt + 1))


def fetch_raw(library_type="base", page_size=100):
    """Every part of the given library type ("base" = Basic), as raw API records."""
    parts, page = [], 1
    while True:
        info = _post({
            "currentPage": page, "pageSize": page_size, "keyword": None,
            "componentLibraryType": library_type, "preferredComponentFlag": False,
            "stockFlag": None, "stockSort": None, "firstSortName": None,
            "secondSortName": None, "componentBrand": None,
            "componentSpecification": None, "componentAttributes": [],
            "searchSource": "search",
        })
        parts += info["list"]
        if not info["list"] or len(parts) >= info["total"]:
            break
        page += 1
    return parts


def normalize(raw):
    return {
        "lcsc": raw["componentCode"],
        "mpn": raw["componentModelEn"],
        "manufacturer": raw["componentBrandEn"],
        "category": raw["firstSortName"],
        "parent_category": raw["secondSortName"],
        "package": raw["componentSpecificationEn"],
        "description": raw["describe"],
        "attributes": {a["attribute_name_en"]: a["attribute_value_name"]
                       for a in raw.get("attributes") or []},
        "datasheet_url": raw.get("dataManualUrl") or "",
        "datasheet_file_id": raw.get("dataManualFileAccessId") or "",
        "jlc_url": f"https://jlcpcb.com/partdetail/{raw['urlSuffix'].replace('/', '-')}"
                   if raw.get("urlSuffix") else "",
    }


def fetch_basic_parts():
    """Normalized Basic parts, sorted by LCSC number."""
    parts = [normalize(r) for r in fetch_raw("base")]
    return sorted(parts, key=lambda p: int(p["lcsc"][1:]))


def download_datasheet(part, dest):
    """Save a part's datasheet PDF to ``dest``; returns False if none is available."""
    if not part.get("datasheet_file_id"):
        return False
    req = urllib.request.Request(FILE_URL + part["datasheet_file_id"], headers=UA)
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                data = r.read()
            break
        except Exception:
            if attempt == 3:
                raise
            time.sleep(5 * (attempt + 1))
    if not data.startswith(b"%PDF"):
        return False
    with open(dest, "wb") as f:
        f.write(data)
    return True
