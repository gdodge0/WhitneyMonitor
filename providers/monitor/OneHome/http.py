"""
GraphQL client for the OneHome ``GetListings`` endpoint.

Unlike the Inyo monitor (which GETs recreation.gov date ranges), OneHome is a
single GraphQL POST authenticated with a Bearer token. The search filters
(bed/bath ranges, property types, listing type, city terms, bounding-box
polygon) are all assembled from the provider's YAML config block — nothing is
hardcoded except the GraphQL query document itself, which is a schema contract
rather than a tunable value.

Header convention matches the rest of the codebase: a browser-style default
header dict with ``Authorization: Bearer <token>`` added by the caller (see
``tools/InyoATC/atc.py``). ``httpx`` sets ``Content-Type: application/json``
automatically from the ``json=`` body.
"""
from __future__ import annotations

import traceback
from json import JSONDecodeError
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

import httpx

if TYPE_CHECKING:
    from .auth import OneHomeAuth

__all__ = [
    "fetch_listings",
    "build_browse_parameter",
    "DEFAULT_HEADERS",
    "GRAPHQL_QUERY",
]

# ---------------------------------------------------------------------------
# Default request headers (imitating a modern Firefox browser)
# ---------------------------------------------------------------------------
DEFAULT_HEADERS: Dict[str, str] = {
    "Accept": "*/*",
    "Accept-Encoding": "gzip, deflate, br, zstd",
    "Accept-Language": "en-US,en;q=0.5",
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "Content-Type": "application/json",
    "Pragma": "no-cache",
    "Origin": "https://www.onehome.com",
    "Referer": "https://www.onehome.com/",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "cross-site",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:137.0) Gecko/20100101 Firefox/137.0",
}

# ---------------------------------------------------------------------------
# GraphQL query document (schema contract — not configurable)
# ---------------------------------------------------------------------------
GRAPHQL_QUERY = (
    "query GetListings($groupId: String!, $browseParameter: BrowseParameter, $includeDislikes: Boolean) {\n"
    "  listings(groupId: $groupId, browseParameter: $browseParameter, includeDislikes: $includeDislikes) {\n"
    "    pageInfo {\n      ...pageInfo\n      __typename\n    }\n"
    "    listings {\n      ...shortListingDetails\n      __typename\n    }\n    __typename\n  }\n}\n\n"
    "fragment pageInfo on PageInfo {\n  totalElements\n  totalPages\n  pageNumber\n  pageSize\n  __typename\n}\n\n"
    "fragment shortListingDetails on ListingDetail {\n  id\n  hideWhenUnauth\n  property {\n"
    "    OriginatingSystemKey\n    OriginatingSystemName\n    StreetAdditionalInfo\n    StreetNumber\n"
    "    StreetDirPrefix\n    StreetName\n    StreetSuffix\n    StreetDirSuffix\n    UnitNumber\n    City\n"
    "    PostalCity\n    StateOrProvince\n    PostalCode\n    PostalCodePlus4\n    InternetAddressDisplayYN\n"
    "    SpecialListingConditions\n    NewConstructionYN\n    ListPrice\n    ListingId\n    LivingArea\n"
    "    PropertyType\n    BedroomsTotal\n    BathroomsTotalInteger\n    LivingAreaTotal\n"
    "    BuildingAreaTotal\n    AvailabilityDate\n    Latitude\n    Longitude\n    LotSizeArea\n"
    "    LotSizeUnits\n    MajorChangeType\n    MajorChangeTimestamp\n    ClosePrice\n    PropertySubType\n"
    "    StructureType\n    PreviousListPrice\n    StandardStatus\n    AboveGradeFinishedArea\n"
    "    AboveGradeFinishedAreaUnits\n    WaterSource\n    Sewer\n    ElectricOnPropertyYN\n"
    "    ActivationDate\n    Utilities\n    CommonInterest\n    Nucleus_SysID\n    DelayedMarketingYN\n"
    "    __typename\n  }\n  media {\n    LongDescription\n    ShortDescription\n    ImageOf\n    MediaKey\n"
    "    MediaType\n    Order\n    Image {\n      Thumbnail {\n        ...imageDetails\n        __typename\n"
    "      }\n      Medium {\n        ...imageDetails\n        __typename\n      }\n      Large {\n"
    "        ...imageDetails\n        __typename\n      }\n      __typename\n    }\n    __typename\n  }\n"
    "  openHouse {\n    OpenHouseDate\n    OpenHouseEndTime\n    OpenHouseStartTime\n    OpenHouseStatus\n"
    "    OpenHouseType\n    OpenHouseLiveStreamURL\n    LivestreamOpenHouseURL\n    Refreshments\n"
    "    __typename\n  }\n  customProperty {\n    ListingKey\n    LivingAreaRange\n    LivingAreaRangeUnits\n"
    "    LotSizeRange\n    BelowGradeFinishedAreaRange\n    BelowGradeUnfinishedAreaRange\n"
    "    AboveGradeFinishedAreaRange\n    AboveGradeUnfinishedAreaRange\n    BuildingAreaTotalRange\n"
    "    FIPSCode\n    FractionalShare\n    BelowGradeBedrooms\n    AboveGradeBedrooms\n    CustomFields {\n"
    "      TotalAvailSqFt\n      __typename\n    }\n    __typename\n  }\n  __typename\n}\n\n"
    "fragment imageDetails on Image {\n  mediaUrl\n  width\n  height\n  size\n  __typename\n}\n"
)

# Config keys that map onto the BOOL city/region search filters.
_CITY_BOOL_FIELDS = [
    "property.CountyOrParish",
    "property.SubdivisionName",
    "property.CityRegion",
    "property.City",
    "property.PostalCity",
]


def build_browse_parameter(cfg: Dict[str, Any], *, page_num: int = 0) -> Dict[str, Any]:
    """Assemble ``variables.browseParameter`` from the provider config block.

    Everything tunable comes from ``cfg``; ``page_num`` is supplied by the
    paginating fetch loop.
    """
    beds = cfg.get("beds", {}) or {}
    baths = cfg.get("baths", {}) or {}
    price = cfg.get("price", {}) or {}

    search_query: List[Dict[str, Any]] = [
        {"type": "LISTING_TYPE", "fieldName": "", "values": [cfg["listing_type"]]},
        {
            "type": "RANGE",
            "fieldName": "property.ListPrice",
            "values": [str(price.get("min", 0)), str(price.get("max", 1000000000))],
        },
        {
            "type": "RANGE",
            "fieldName": "property.BedroomsTotal",
            "values": [str(beds.get("min", 0)), str(beds.get("max", 1000000))],
        },
        {
            "type": "RANGE",
            "fieldName": "property.BathroomsTotalInteger",
            "values": [str(baths.get("min", 0)), str(baths.get("max", 1000000))],
        },
        {"type": "PROPERTY_TYPE", "fieldName": "", "values": list(cfg.get("property_types", []))},
        {"type": "TERMS", "fieldName": "property.StateOrProvince", "values": [cfg.get("state", "CA")]},
    ]

    # City/region BOOL filters — one per configured term per field.
    for term in cfg.get("city_terms", []):
        for field_name in _CITY_BOOL_FIELDS:
            search_query.append({"type": "BOOL", "fieldName": field_name, "values": [term]})

    # Polygon: list of [lat, lon] pairs → list of {latitude, longitude}.
    polygon = [
        {"latitude": pt[0], "longitude": pt[1]}
        for pt in cfg.get("polygon", [])
    ]

    return {
        "searchQuery": search_query,
        "sort": {"name": "property.MajorChangeTimestamp", "order": "DESC"},
        "pageInput": {"pageNum": page_num, "size": cfg.get("page_size", 25)},
        "polygon": polygon,
    }


def _build_payload(cfg: Dict[str, Any], *, page_num: int) -> Dict[str, Any]:
    return {
        "operationName": "GetListings",
        "variables": {
            "browseParameter": build_browse_parameter(cfg, page_num=page_num),
            "groupId": "",
            "includeDislikes": False,
        },
        "query": GRAPHQL_QUERY,
    }


async def fetch_listings(
    url: str,
    auth: "OneHomeAuth",
    cfg: Dict[str, Any],
    *,
    max_pages: int = 10,
    timeout: float = 15.0,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Fetch all matching listings, paginating up to ``max_pages``.

    ``auth`` mints the Bearer JWT (from a share token) via :meth:`OneHomeAuth.get_token`.
    If a page returns HTTP 401 (expired session) we refresh the token once and
    retry that page; a ``refreshed`` guard prevents an infinite loop.

    Returns ``(listings, errors)`` — ``listings`` is a flat list of listing
    dicts (each with ``id``, ``property``, ``media`` …); ``errors`` is a list of
    human-readable error strings. Partial results are returned on per-page
    failure so a single bad page doesn't lose the whole scan.
    """
    try:
        token = await auth.get_token()
    except Exception as exc:  # noqa: BLE001
        return [], [f"[auth] {exc}"]

    def _headers(tok: str) -> Dict[str, str]:
        h = dict(DEFAULT_HEADERS)
        h["Authorization"] = f"Bearer {tok}"
        return h

    listings: List[Dict[str, Any]] = []
    errors: List[str] = []

    async with httpx.AsyncClient(timeout=timeout) as client:
        page_num = 0
        total_pages = 1  # updated after the first response
        refreshed = False
        while page_num < total_pages and page_num < max_pages:
            page_listings, page_total, err, status = await _fetch_page(
                client, url, _headers(token), cfg, page_num
            )

            if status == 401 and not refreshed:
                # Session JWT likely expired — mint a fresh one and retry this page.
                refreshed = True
                try:
                    token = await auth.refresh(after=token)
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"[auth] refresh failed: {exc}")
                    break
                continue

            if err is not None:
                errors.append(err)
                break  # stop paginating on error; keep what we have

            listings.extend(page_listings)
            total_pages = page_total
            page_num += 1

    return listings, errors


async def _fetch_page(
    client: httpx.AsyncClient,
    url: str,
    headers: Dict[str, str],
    cfg: Dict[str, Any],
    page_num: int,
) -> Tuple[List[Dict[str, Any]], int, Optional[str], int]:
    """Fetch a single page. Returns ``(listings, total_pages, error, status)``."""
    try:
        resp = await client.post(url, json=_build_payload(cfg, page_num=page_num), headers=headers)
    except Exception as exc:  # noqa: BLE001
        return [], 0, f"[request] page {page_num}: {''.join(traceback.format_exception(exc))}", 0

    if resp.status_code >= 400:
        # e.g. 401 with an expired/invalid Bearer token. Surface as an error so
        # callers don't mistake it for "0 listings" and wipe the baseline.
        return [], 0, f"[http {resp.status_code}] page {page_num}: {resp.text[:300]}", resp.status_code

    try:
        body = resp.json()
    except JSONDecodeError:
        return [], 0, f"[json] page {page_num}: status {resp.status_code} body {resp.text[:300]}", resp.status_code

    if body.get("errors"):
        return [], 0, f"[graphql] page {page_num}: {body['errors']}", resp.status_code

    listings_node = (body.get("data") or {}).get("listings") or {}
    page_info = listings_node.get("pageInfo") or {}
    total_pages = int(page_info.get("totalPages") or 0)
    return listings_node.get("listings") or [], total_pages, None, resp.status_code
