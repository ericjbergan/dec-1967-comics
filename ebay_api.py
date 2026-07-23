"""
Lightweight eBay Browse API client for searching Silver Age comics.

Simpler than the book-catalog version — no format-detection filtering,
no ChatGPT edition matching, no price-estimate logic. Just: give me the
Buy It Now + auction listings that match "Amazing Spider-Man #55" so I
can eyeball prices and click through.
"""
import base64
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Tuple

import requests

# eBay category: 63 = "Comic Books" under Collectibles > Comics.
# We keep it wide so cross-listed and category-tagged items still surface.
COMIC_CATEGORY_IDS = "63"

# ZIP used to force calculated-shipping quotes. NYC keeps costs middle-of-the-road
# for a US buyer; the actual buyer's ZIP would give a slightly different number.
DEFAULT_SHIPPING_ZIP = "10001"


class eBayComicSearch:
    PROD_TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"
    PROD_BROWSE_URL = "https://api.ebay.com/buy/browse/v1/item_summary/search"
    PROD_ITEM_URL = "https://api.ebay.com/buy/browse/v1/item/"
    SANDBOX_TOKEN_URL = "https://api.sandbox.ebay.com/identity/v1/oauth2/token"
    SANDBOX_BROWSE_URL = "https://api.sandbox.ebay.com/buy/browse/v1/item_summary/search"
    SANDBOX_ITEM_URL = "https://api.sandbox.ebay.com/buy/browse/v1/item/"

    def __init__(self, app_id: str, cert_id: str, sandbox: bool = False):
        self.app_id = app_id
        self.cert_id = cert_id
        self.sandbox = sandbox
        self.token_url = self.SANDBOX_TOKEN_URL if sandbox else self.PROD_TOKEN_URL
        self.browse_url = self.SANDBOX_BROWSE_URL if sandbox else self.PROD_BROWSE_URL
        self.item_url = self.SANDBOX_ITEM_URL if sandbox else self.PROD_ITEM_URL
        self._token: Optional[str] = None
        self._token_expires: Optional[datetime] = None

    def _get_token(self) -> str:
        if self._token and self._token_expires and datetime.now() < self._token_expires:
            return self._token

        creds = base64.b64encode(f"{self.app_id}:{self.cert_id}".encode()).decode()
        resp = requests.post(
            self.token_url,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Authorization": f"Basic {creds}",
            },
            data={
                "grant_type": "client_credentials",
                "scope": "https://api.ebay.com/oauth/api_scope",
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        self._token = data["access_token"]
        expires_in = int(data.get("expires_in", 7200))
        self._token_expires = datetime.now() + timedelta(seconds=expires_in - 200)
        return self._token

    def search(
        self,
        publisher: str,
        title: str,
        issue_number: str,
        year: int = 1967,
        limit: int = 25,
    ) -> List[Dict]:
        """
        Query eBay for a specific comic issue and return simplified listings.

        Query format: '{title} #{issue} {year} {publisher}'. The year is
        essential for Silver Age books — without it, modern reprints of
        the same issue number drown out the original.
        """
        token = self._get_token()

        # Strip leading "The " from titles to widen matches
        display_title = title
        if title.lower().startswith("the "):
            display_title = title[4:]

        # Quote the title so eBay treats it as a phrase, not loose words.
        # For a common phrase like "Secret Wars", unquoted searches surface
        # every tie-in ("Secret Wars 2099", "Secret Wars: Battleworld", …).
        query = f'"{display_title}" #{issue_number} {year} {publisher}'.strip()

        resp = requests.get(
            self.browse_url,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "X-EBAY-C-MARKETPLACE-ID": "EBAY_US",
            },
            params={
                "q": query,
                "limit": 200,  # fetch max so post-filter has room
                "category_ids": COMIC_CATEGORY_IDS,
                "filter": "deliveryCountry:US",
                # No sort — use eBay's bestMatch relevance so tie-in cheap
                # junk doesn't drown out real main-series listings. We sort
                # by price client-side after our strict-title filter runs.
            },
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()

        # Build a regex requiring the listing title to START with "{title}"
        # (optionally after a bland brand/date prefix), then optional year in
        # parens, then "#{issue}". This anchors the match at the start so
        # tie-in prefixes like "CIVIL WAR:", "Battleworld Runaways - ",
        # or "Marvel: X-Tinction Agenda - " get rejected — they push our
        # title into the middle of the listing name.
        title_pat = re.escape(display_title)
        issue_pat = re.escape(issue_number) if issue_number else r"\d+"
        prefix_pat = (
            r"(?:"
            r"marvel(?:\s+comics)?\s+|"
            r"dc(?:\s+comics)?\s+|"
            r"\d{4}\s+(?:marvel|dc)(?:\s+comics)?\s+|"
            r"the\s+"
            r")?"
        )
        strict_re = re.compile(
            rf"^\s*{prefix_pat}{title_pat}\s+(?:\(\d{{4}}\)\s+)?#\s*{issue_pat}\b",
            re.IGNORECASE,
        )
        # Drop retailer-incentive and variant-cover listings — they skew price
        # heavily and rarely match what a normal collector has on the shelf.
        variant_re = re.compile(
            r"\b(?:variant|incentive|sketch\s+variant|blank\s+sketch|"
            r"virgin\s+cover|foil|1:\d+|custom\s+edition|action\s+figure)\b",
            re.IGNORECASE,
        )
        # Drop professionally graded/slabbed copies — they carry a big
        # premium unrelated to raw condition.
        graded_re = re.compile(
            r"\b(?:cgc|cbcs|pgx|egs|graded|slabbed|slab)\b",
            re.IGNORECASE,
        )
        # Drop titles that flag known low/damaged condition. Includes
        # explicit descriptors ("coverless", "missing cover"), incomplete-
        # book markers ("water damage", "torn", "reader copy"), and raw
        # grade shorthand at the low end (Fair through Very Good-minus,
        # i.e. numeric grades <= 4.5 or the letters Fair/Poor/GD/GD+).
        poor_re = re.compile(
            r"\b(?:"
            r"missing\s+cover|no\s+cover|coverless|"
            r"missing\s+pages?|incomplete|"
            r"water\s+damage[d]?|water[- ]?stain[ed]*|"
            r"torn|tape|taped|repaired|restored|"
            r"reader\s+copy|reading\s+copy|"
            r"poor(?:\s+condition)?|fair(?:\s+condition)?|"
            r"low\s+grade|"
            r"pr\s*0\.5|fr\s*1\.0|gd\s*1\.5|gd\+?\s*2\.0|"
            r"vg-\s*3\.5|vg\s*4\.0|vg\+?\s*4\.5|"
            r"(?:^|[^\d.])[0-4]\.[05]\s*(?:$|[^\d])"
            r")\b",
            re.IGNORECASE,
        )

        results = []
        for item in data.get("itemSummaries", []):
            listing_title = item.get("title", "")
            if not strict_re.search(listing_title):
                continue
            if variant_re.search(listing_title):
                continue
            if graded_re.search(listing_title):
                continue
            if poor_re.search(listing_title):
                continue
            if len(results) >= limit:
                break
            price = item.get("price") or {}
            image = item.get("image") or {}
            thumbnails = item.get("thumbnailImages") or []
            thumb_url = image.get("imageUrl")
            if not thumb_url and thumbnails:
                thumb_url = thumbnails[0].get("imageUrl")

            buying_options = item.get("buyingOptions") or []

            price_val = _to_float(price.get("value"))
            shipping_val, shipping_free = _extract_shipping(item)
            total_val = None
            if price_val is not None and shipping_val is not None:
                total_val = price_val + shipping_val

            results.append({
                "item_id": item.get("itemId"),
                "title": item.get("title", ""),
                "price": price_val,
                "shipping": shipping_val,
                "shipping_free": shipping_free,
                "total": total_val,
                "currency": price.get("currency", "USD"),
                "condition": item.get("condition") or "Unknown",
                "buying_options": buying_options,
                "url": item.get("itemWebUrl", ""),
                "thumbnail": thumb_url,
                "seller": (item.get("seller") or {}).get("username", ""),
            })

        self._fill_missing_shipping(results)
        # Sort by total price ascending (nulls last) so cheapest bubble up
        results.sort(key=lambda r: (r.get("total") is None, r.get("total") or 0))
        return results

    def _fill_missing_shipping(self, results: List[Dict]) -> None:
        """Fetch item details in parallel for any listing whose summary
        didn't include a shipping amount (calculated-shipping listings)."""
        needs = [r for r in results if r["shipping"] is None and r.get("item_id")]
        if not needs:
            return

        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = {
                pool.submit(self._fetch_item_shipping, r["item_id"]): r
                for r in needs
            }
            for future in futures:
                shipping, free = future.result()
                r = futures[future]
                if shipping is None:
                    continue
                r["shipping"] = shipping
                r["shipping_free"] = free
                if r["price"] is not None:
                    r["total"] = r["price"] + shipping

    def _fetch_item_shipping(self, item_id: str) -> Tuple[Optional[float], bool]:
        """Fetch full item details to resolve calculated shipping for a US ZIP."""
        try:
            token = self._get_token()
            resp = requests.get(
                f"{self.item_url}{item_id}",
                headers={
                    "Authorization": f"Bearer {token}",
                    "X-EBAY-C-MARKETPLACE-ID": "EBAY_US",
                    "X-EBAY-C-ENDUSERCTX":
                        f"contextualLocation=country=US,zip={DEFAULT_SHIPPING_ZIP}",
                },
                timeout=8,
            )
            resp.raise_for_status()
            return _extract_shipping(resp.json())
        except requests.RequestException:
            return (None, False)


def _to_float(v):
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _extract_shipping(item):
    """
    Pull shipping cost from an item summary or full item response.

    Returns (shipping_amount, is_free). Unknown returns (None, False).
    Free returns (0.0, True). A specific cost returns (amount, False).
    Accepts any option with a numeric shippingCost — FIXED, CALCULATED with
    a resolved value (present when the request carried a ZIP context), or
    FREE / zero cost.
    """
    for option in item.get("shippingOptions") or []:
        cost_type = (option.get("shippingCostType") or "").upper()
        cost = option.get("shippingCost") or {}
        val = _to_float(cost.get("value"))
        if val is not None:
            return (val, val == 0.0)
        if "FREE" in cost_type:
            return (0.0, True)
    return (None, False)
