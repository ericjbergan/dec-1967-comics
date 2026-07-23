"""
Lightweight eBay Browse API client for searching Silver Age comics.

Simpler than the book-catalog version — no format-detection filtering,
no ChatGPT edition matching, no price-estimate logic. Just: give me the
Buy It Now + auction listings that match "Amazing Spider-Man #55" so I
can eyeball prices and click through.
"""
import base64
from datetime import datetime, timedelta
from typing import Optional, List, Dict

import requests

# eBay category: 63 = "Comic Books" under Collectibles > Comics.
# We keep it wide so cross-listed and category-tagged items still surface.
COMIC_CATEGORY_IDS = "63"


class eBayComicSearch:
    PROD_TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"
    PROD_BROWSE_URL = "https://api.ebay.com/buy/browse/v1/item_summary/search"
    SANDBOX_TOKEN_URL = "https://api.sandbox.ebay.com/identity/v1/oauth2/token"
    SANDBOX_BROWSE_URL = "https://api.sandbox.ebay.com/buy/browse/v1/item_summary/search"

    def __init__(self, app_id: str, cert_id: str, sandbox: bool = False):
        self.app_id = app_id
        self.cert_id = cert_id
        self.sandbox = sandbox
        self.token_url = self.SANDBOX_TOKEN_URL if sandbox else self.PROD_TOKEN_URL
        self.browse_url = self.SANDBOX_BROWSE_URL if sandbox else self.PROD_BROWSE_URL
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

        query = f"{display_title} #{issue_number} {year} {publisher}".strip()

        resp = requests.get(
            self.browse_url,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "X-EBAY-C-MARKETPLACE-ID": "EBAY_US",
            },
            params={
                "q": query,
                "limit": min(limit, 50),
                "category_ids": COMIC_CATEGORY_IDS,
                "filter": "deliveryCountry:US",
                "sort": "price",
            },
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()

        results = []
        for item in data.get("itemSummaries", []):
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

        return results


def _to_float(v):
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _extract_shipping(item):
    """
    Pull shipping cost from an item summary.

    Returns (shipping_amount, is_free). Unknown shipping returns (None, False).
    Free returns (0.0, True). A specific cost returns (amount, False).
    """
    for option in item.get("shippingOptions") or []:
        cost_type = (option.get("shippingCostType") or "").upper()
        cost = option.get("shippingCost") or {}
        val = _to_float(cost.get("value"))
        if cost_type == "FIXED" and val is not None:
            return (val, val == 0.0)
        if "FREE" in cost_type:
            return (0.0, True)
        if val == 0.0:
            return (0.0, True)
    return (None, False)
