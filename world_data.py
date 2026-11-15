"""Outside data for the country rundown: Treasury yields, Treasury ETF prices (Alpaca),
commodity futures (Yahoo) and
SEC EDGAR filings by where the filer is based. Keyless except Alpaca."""

import csv
import io
import math
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from functools import wraps

import httpx

TIMEOUT = httpx.Timeout(15.0)


def ttl_cache(seconds: int):
    """Cache a function's result per argument tuple for `seconds` (per process)."""

    def deco(fn):
        store: dict = {}
        lock = threading.Lock()

        @wraps(fn)
        def inner(*args, **kwargs):
            key = (args, tuple(sorted(kwargs.items())))
            with lock:
                hit = store.get(key)
            if hit and time.monotonic() - hit[0] < seconds:
                return hit[1]
            value = fn(*args, **kwargs)
            with lock:
                store[key] = (time.monotonic(), value)
            return value

        inner.cache_clear = store.clear  # type: ignore[attr-defined]
        return inner

    return deco


# --- Reverse geocoding (OpenStreetMap Nominatim, no key) ----------------------------------------

PLACE_KEYS = ("city", "town", "village", "municipality", "county", "state_district", "state", "region", "province", "country")


@ttl_cache(24 * 3600)
def reverse_geocode(lat: float, lon: float) -> dict:
    """Nominatim address parts for a point, e.g. {"city": "Lyon", "state": "Auvergne-Rhône-Alpes", ...}."""
    r = httpx.get("https://nominatim.openstreetmap.org/reverse", timeout=TIMEOUT,
                  params={"lat": round(lat, 3), "lon": round(lon, 3), "format": "jsonv2", "addressdetails": 1, "zoom": 10},
                  headers={"User-Agent": "nostos-dev research tool"})  # Nominatim policy requires a real UA
    r.raise_for_status()
    return r.json().get("address") or {}


def local_places(address: dict) -> list[str]:
    """Place names from a Nominatim address, most specific first, ending with the country."""
    return list(dict.fromkeys(address[k] for k in PLACE_KEYS if address.get(k)))


# --- Treasury par yield curve (treasury.gov, daily, no key) -----------------------------------

TENORS = {  # CSV column -> years
    "1 Mo": 1 / 12, "2 Mo": 2 / 12, "3 Mo": 0.25, "4 Mo": 4 / 12, "6 Mo": 0.5, "1 Yr": 1,
    "2 Yr": 2, "3 Yr": 3, "5 Yr": 5, "7 Yr": 7, "10 Yr": 10, "20 Yr": 20, "30 Yr": 30,
}
TREASURY_CSV = (
    "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
    "daily-treasury-rates.csv/{year}/all?type=daily_treasury_yield_curve&field_tdr_date_value={year}&_format=csv"
)


def _treasury_rows(year: int) -> list[dict]:
    # treasury.gov's CDN stalls requests carrying httpx's default User-Agent.
    r = httpx.get(TREASURY_CSV.format(year=year), timeout=TIMEOUT, follow_redirects=True,
                  headers={"User-Agent": "nostos-dev research tool"})
    r.raise_for_status()
    return list(csv.DictReader(io.StringIO(r.text)))


def _curve(row: dict) -> list[dict]:
    return [
        {"tenor": col, "years": round(years, 4), "yield": float(row[col])}
        for col, years in TENORS.items()
        if (row.get(col) or "").strip()
    ]


@ttl_cache(1800)
def treasury_curve() -> dict:
    """Latest US Treasury par yield curve, the curve about a month earlier, and the daily 2Y/10Y path."""
    today = date.today()
    rows = _treasury_rows(today.year)
    if len(rows) < 25:  # early January: reach into last year for the month-ago curve
        rows += _treasury_rows(today.year - 1)
    rows = [r for r in rows if r.get("Date")]
    for r in rows:
        r["_date"] = datetime.strptime(r["Date"], "%m/%d/%Y").date()
    rows.sort(key=lambda r: r["_date"], reverse=True)
    latest = rows[0]
    month_ago = next((r for r in rows if r["_date"] <= latest["_date"] - timedelta(days=30)), rows[-1])
    recent = [r for r in rows if r["_date"] > latest["_date"] - timedelta(days=31)]
    return {
        "source": "U.S. Treasury daily par yield curve",
        "as_of": latest["_date"].isoformat(),
        "curve": _curve(latest),
        "month_ago": {"as_of": month_ago["_date"].isoformat(), "curve": _curve(month_ago)},
        "history": [
            {"date": r["_date"].isoformat(), "2 Yr": float(r["2 Yr"]), "10 Yr": float(r["10 Yr"])}
            for r in reversed(recent)
            if r.get("2 Yr") and r.get("10 Yr")
        ],
    }


# --- Government bond yields worldwide (TradingView TVC scanner, keyless) ----------------------

TV_SCAN = "https://scanner.tradingview.com/global/scan"
SOVEREIGN_YIELDS = {  # Natural Earth name -> TradingView TVC country code (every country TVC covers)
    "Australia": "AU", "Austria": "AT", "Belgium": "BE", "Brazil": "BR", "Canada": "CA", "Chile": "CL",
    "China": "CN", "Colombia": "CO", "Czechia": "CZ", "Denmark": "DK", "Finland": "FI", "France": "FR",
    "Germany": "DE", "Greece": "GR", "Hong Kong": "HK", "Hungary": "HU", "Iceland": "IS",
    "India": "IN", "Indonesia": "ID", "Ireland": "IE", "Israel": "IL", "Italy": "IT", "Japan": "JP",
    "Lithuania": "LT", "Malaysia": "MY", "Mexico": "MX", "Netherlands": "NL", "New Zealand": "NZ",
    "Norway": "NO", "Peru": "PE", "Philippines": "PH", "Poland": "PL", "Portugal": "PT",
    "Romania": "RO", "Russia": "RU", "Singapore": "SG", "Slovakia": "SK", "South Africa": "ZA",
    "South Korea": "KR", "Spain": "ES", "Sweden": "SE", "Switzerland": "CH", "Taiwan": "TW",
    "Thailand": "TH", "Turkey": "TR", "Ukraine": "UA", "United Kingdom": "GB",
    "United States of America": "US", "Vietnam": "VN",
}
EURO_AREA = {"Estonia", "Latvia", "Luxembourg", "Slovenia", "Croatia", "Cyprus", "Malta"}  # no own TVC series: euro-area curve


@ttl_cache(1800)
def _tv_yields() -> dict[str, list[dict]]:
    """Every TVC government bond yield in one request, grouped by country code."""
    r = httpx.post(TV_SCAN, timeout=TIMEOUT, headers={"User-Agent": "Mozilla/5.0"}, json={
        "filter": [{"left": "exchange", "operation": "equal", "right": "TVC"},
                   {"left": "description", "operation": "match", "right": "Government Bonds Yield"}],
        "columns": ["description", "close", "Perf.1M"], "range": [0, 2000]})
    r.raise_for_status()
    out: dict[str, list[dict]] = {}
    for row in r.json().get("data") or []:
        m = re.fullmatch(r"TVC:([A-Z]{2})(\d{2})(M?)Y", row["s"])
        name, close, perf = row["d"]
        if not m or close is None:
            continue
        n, months = int(m[2]), bool(m[3])
        past = close / (1 + perf / 100) if perf is not None and abs(1 + perf / 100) > 0.05 else None
        out.setdefault(m[1], []).append({
            "symbol": row["s"], "name": name, "tenor": f"{n} Mo" if months else f"{n} Yr",
            "years": n / 12 if months else n, "yield": round(close, 3),
            "month_ago": None if past is None else round(past, 3),
            "change_1m_bp": None if past is None else round((close - past) * 100, 1),
        })
    for bonds in out.values():
        bonds.sort(key=lambda b: b["years"])
    return out


def sovereign_curve(country: str) -> dict:
    """A country's government yield curve now and about a month ago (from TradingView's 1-month
    change), plus each bond. Euro members without their own series get the euro-area curve."""
    code = SOVEREIGN_YIELDS.get(country) or ("EU" if country in EURO_AREA else None)
    bonds = _tv_yields().get(code or "", [])
    if not bonds:
        raise ValueError(f"No government bond yields for {country}")
    today = date.today()
    return {
        "source": "TradingView government bond yields" + (" (euro-area curve)" if code == "EU" else ""),
        "as_of": today.isoformat(),
        "curve": [{"tenor": b["tenor"], "years": b["years"], "yield": b["yield"]} for b in bonds],
        "month_ago": {"as_of": (today - timedelta(days=30)).isoformat(), "curve": [
            {"tenor": b["tenor"], "years": b["years"], "yield": b["month_ago"]} for b in bonds if b["month_ago"] is not None]},
        "bonds": bonds,
    }


def has_sovereign_yields(country: str) -> bool:
    return country in SOVEREIGN_YIELDS or country in EURO_AREA


# --- Treasury ETF prices by maturity bucket (Alpaca market data) ------------------------------

TREASURY_ETFS = {
    "SGOV": "0-3 month bills",
    "SHY": "1-3 year",
    "IEI": "3-7 year",
    "IEF": "7-10 year",
    "TLH": "10-20 year",
    "TLT": "20+ year",
}


def _alpaca_funds(funds: dict[str, str], days: int) -> list[dict]:
    """Daily closes and % change over `days` for {symbol: label} from Alpaca's IEX stock feed."""
    key = os.getenv("ALPACA_API_KEY", "").strip()
    secret = os.getenv("ALPACA_SECRET_KEY", "").strip()
    if not key or not secret:
        raise RuntimeError("ALPACA_API_KEY and ALPACA_SECRET_KEY must be set")
    start = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
    r = httpx.get(
        "https://data.alpaca.markets/v2/stocks/bars",
        params={"symbols": ",".join(funds), "timeframe": "1Day", "start": start,
                "feed": "iex", "limit": 10000, "adjustment": "all"},
        headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret},
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    bars = r.json().get("bars") or {}
    out = []
    for symbol, label in funds.items():
        closes = [{"date": b["t"][:10], "close": b["c"]} for b in bars.get(symbol) or []]
        change = (closes[-1]["close"] / closes[0]["close"] - 1) * 100 if len(closes) > 1 else None
        out.append({"symbol": symbol, "label": label, "closes": closes,
                    "change_pct": round(change, 2) if change is not None else None})
    return out


@ttl_cache(900)
def treasury_etf_trend(days: int = 30) -> dict:
    """Daily closes for one iShares Treasury ETF per maturity bucket over the last `days` days."""
    return {"source": "Alpaca (IEX feed), iShares Treasury ETFs", "days": days,
            "funds": _alpaca_funds(TREASURY_ETFS, days)}


# --- Commodity futures by region (Yahoo Finance chart API, front month, no key) ---------------

# commodity -> (symbol, benchmark, region, settlement). Keys double as news topic tags (news.TOPICS).
COMMODITY_FUTURES = {  # (Yahoo symbol, name, pricing point, settlement, physical unit); every series Yahoo has with history
    "oil": [
        ("BZ=F", "Brent crude", "North Sea (ICE)", "cash", "bbl"),
        ("CL=F", "WTI crude", "Cushing, Oklahoma (NYMEX)", "physical", "bbl"),
        ("DBL=F", "Dubai crude", "Arabian Gulf (Platts, NYMEX)", "cash", "bbl"),
        ("HO=F", "ULSD heating oil", "New York Harbor (NYMEX)", "physical", "gal"),
        ("RB=F", "RBOB gasoline", "New York Harbor (NYMEX)", "physical", "gal"),
    ],
    "natural-gas": [
        ("NG=F", "Henry Hub", "Erath, Louisiana (NYMEX)", "physical", "MMBtu"),
        ("TTF=F", "Dutch TTF", "Netherlands virtual hub (ICE Endex)", "physical", "MWh"),
        ("JKM=F", "JKM LNG", "Delivered Japan / Korea / China / Taiwan (Platts, NYMEX)", "cash", "MMBtu"),
    ],
    "metals": [
        ("GC=F", "Gold", "COMEX, New York vaults", "physical", "oz"),
        ("SI=F", "Silver", "COMEX, New York vaults", "physical", "oz"),
        ("PL=F", "Platinum", "NYMEX, New York / Zurich vaults", "physical", "oz"),
        ("PA=F", "Palladium", "NYMEX, New York / Zurich vaults", "physical", "oz"),
        ("HG=F", "Copper", "COMEX, US warehouses", "physical", "lb"),
        ("ALI=F", "Aluminum", "COMEX, US warehouses", "physical", "t"),
        ("HRC=F", "Hot-rolled steel coil", "US Midwest (CRU index, COMEX)", "cash", "short t"),
        ("TIO=F", "Iron ore 62% Fe", "CFR Qingdao, China (COMEX)", "cash", "t"),
    ],
    "agriculture": [
        ("ZW=F", "Chicago SRW wheat", "Chicago / Toledo (CBOT)", "physical", "bu"),
        ("KE=F", "KC HRW wheat", "Kansas City (CBOT)", "physical", "bu"),
        ("ZC=F", "Corn", "Illinois River (CBOT)", "physical", "bu"),
        ("ZS=F", "Soybeans", "Illinois River (CBOT)", "physical", "bu"),
        ("ZM=F", "Soybean meal", "Central US processors (CBOT)", "physical", "short t"),
        ("ZL=F", "Soybean oil", "Central US processors (CBOT)", "physical", "lb"),
        ("ZO=F", "Oats", "Minneapolis / Chicago (CBOT)", "physical", "bu"),
        ("ZR=F", "Rough rice", "Arkansas (CBOT)", "physical", "cwt"),
        ("KC=F", "Arabica coffee", "ICE US licensed warehouses", "physical", "lb"),
        ("CC=F", "Cocoa", "ICE US, Delaware River ports", "physical", "t"),
        ("SB=F", "Raw sugar #11", "FOB origin, world raw cane (ICE)", "physical", "lb"),
        ("CT=F", "Cotton", "US Southern warehouses (ICE)", "physical", "lb"),
        ("OJ=F", "Orange juice (FCOJ)", "Florida (ICE)", "physical", "lb"),
        ("LBR=F", "Lumber", "US / Canada West mills (CME)", "physical", "1000 bd ft"),
    ],
    "livestock": [
        ("HE=F", "Lean hogs", "US Midwest (CME index)", "cash", "lb"),
        ("LE=F", "Live cattle", "US Plains feedlots (CME)", "physical", "lb"),
        ("GF=F", "Feeder cattle", "US Plains (CME index)", "cash", "lb"),
        ("DC=F", "Class III milk", "US dairy (CME, USDA price)", "cash", "cwt"),
        ("CSC=F", "Cheese", "US dairy (CME, USDA price)", "cash", "lb"),
    ],
}
SPREADS = {"oil": ("BZ=F", "CL=F")}  # same unit, same grade family: Brent - WTI
UNITS = {"USX": "US cents", "USD": "USD", "EUR": "EUR"}


def _yahoo_closes(symbol: str, days: int) -> tuple[str, list[dict]]:
    # shortcut: Yahoo's chart API is unofficial and can change or rate-limit; swap for a licensed feed in production.
    r = httpx.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
                  params={"range": f"{days}d", "interval": "1d"}, timeout=TIMEOUT,
                  headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    result = (r.json().get("chart", {}).get("result") or [None])[0]
    if not result:
        raise RuntimeError(f"Yahoo returned no data for {symbol}")
    quote = result["indicators"]["quote"][0]
    closes = [
        {"date": datetime.fromtimestamp(t, timezone.utc).date().isoformat(), "close": round(c, 4)}
        for t, c in zip(result.get("timestamp") or [], quote.get("close") or [])
        if c is not None
    ]
    return result["meta"].get("currency", ""), closes


@ttl_cache(900)
def commodity_futures(commodity: str, days: int = 30) -> dict:
    """Front-month futures for one commodity, one series per regional benchmark, with settlement type."""
    if commodity not in COMMODITY_FUTURES:
        raise ValueError(f"commodity must be one of {', '.join(COMMODITY_FUTURES)}")
    specs = COMMODITY_FUTURES[commodity]
    with ThreadPoolExecutor(len(specs)) as pool:
        fetched = list(pool.map(lambda spec: _yahoo_closes(spec[0], days), specs))
    contracts = []
    for (symbol, name, region, settlement, per), (currency, closes) in zip(specs, fetched):
        change = (closes[-1]["close"] / closes[0]["close"] - 1) * 100 if len(closes) > 1 else None
        contracts.append({
            "symbol": symbol, "name": name, "region": region, "settlement": settlement,
            "unit": f"{UNITS.get(currency or 'USD', currency)}/{per}", "price": closes[-1]["close"] if closes else None,
            "closes": closes, "change_pct": round(change, 2) if change is not None else None,
        })
    spread = None
    by = {c["symbol"]: c for c in contracts}
    pair = SPREADS.get(commodity)
    if pair and all(by[s]["price"] is not None for s in pair):
        a, b = by[pair[0]], by[pair[1]]
        spread = {"name": f"{a['name']} - {b['name']}", "value": round(a["price"] - b["price"], 2), "unit": a["unit"]}
    return {"source": "Yahoo Finance, front-month futures", "commodity": commodity, "days": days,
            "contracts": contracts, "spread": spread}


# --- SEC EDGAR filings by the filer's location (full-text search, no key) ---------------------

# EDGAR location codes, keyed by the Natural Earth names the globe uses. Verified against live hits.
EDGAR_LOCATIONS = {
    "United Kingdom": "X0", "Germany": "2M", "France": "I0", "Japan": "M0", "China": "F4",
    "India": "K7", "Israel": "L3", "Brazil": "D5", "Mexico": "O5", "Argentina": "C1",
    "Australia": "C3", "Switzerland": "V8", "Netherlands": "P7", "Ireland": "L2",
    "South Korea": "M5", "Taiwan": "F5", "Sweden": "V7", "Norway": "Q8", "Denmark": "G7",
    "Spain": "U3", "Italy": "L6", "Belgium": "C9", "Chile": "F3", "Colombia": "F8", "Peru": "R5",
    "South Africa": "T3", "Luxembourg": "N4", "Greece": "J3", "Turkey": "W8", "Indonesia": "K8",
    "Philippines": "R6", "Finland": "H9", "Canada": "A0,A1,A6,A8", "Hong Kong": "K3", "Singapore": "U0",
}
US = "United States of America"
FOREIGN_FORMS = "6-K,20-F,40-F"
US_FORMS = "8-K"


@ttl_cache(900)
def edgar_filings(country: str, query: str | None = None, days: int = 30, limit: int = 15) -> dict:
    """Recent SEC filings by companies based in `country` (6-K/20-F/40-F; 8-K for the US),
    optionally matching a full-text `query` such as "bankruptcy" or "sanctions"."""
    if country != US and country not in EDGAR_LOCATIONS:
        return {"country": country, "supported": False, "filings": []}
    end = date.today()
    params = {
        "forms": US_FORMS if country == US else FOREIGN_FORMS,
        "dateRange": "custom",
        "startdt": (end - timedelta(days=days)).isoformat(),
        "enddt": end.isoformat(),
    }
    if country != US:
        params["locationCodes"] = EDGAR_LOCATIONS[country]
    if query:
        params["q"] = f'"{query}"'
    r = httpx.get(
        "https://efts.sec.gov/LATEST/search-index",
        params=params,
        # SEC asks automated clients to identify themselves; set SEC_USER_AGENT to "<app> <contact>".
        headers={"User-Agent": os.getenv("SEC_USER_AGENT", "nostos-dev research tool")},
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    hits = r.json().get("hits") or {}
    seen, filings = set(), []
    for h in hits.get("hits") or []:
        s = h.get("_source") or {}
        adsh, _, filename = (h.get("_id") or "").partition(":")
        company = (s.get("display_names") or ["?"])[0].split("  (")[0]
        key = (company, s.get("file_date"), s.get("form"))  # one row per company/day/form, not per exhibit
        if not adsh or key in seen or not s.get("ciks"):
            continue
        seen.add(key)
        cik = int(s["ciks"][0])
        filings.append({
            "company": company,
            "form": s.get("form") or s.get("file_type"),
            "filed": s.get("file_date"),
            "description": s.get("file_description"),
            "location": (s.get("biz_locations") or [None])[0],
            "url": f"https://www.sec.gov/Archives/edgar/data/{cik}/{adsh.replace('-', '')}/{filename}",
        })
    filings.sort(key=lambda f: f["filed"] or "", reverse=True)
    return {"country": country, "supported": True, "total": (hits.get("total") or {}).get("value"),
            "filings": filings[:limit]}


# --- Commodity ETFs (Alpaca) --------------------------------------------------------------------

COMMODITY_ETFS = {
    "oil": {"USO": "WTI crude", "BNO": "Brent crude"},
    "natural-gas": {"UNG": "US natural gas", "BOIL": "2x natural gas"},
    "metals": {"GLD": "Gold", "SLV": "Silver", "CPER": "Copper"},
    "agriculture": {"WEAT": "Wheat", "CORN": "Corn", "SOYB": "Soybeans", "DBA": "Agriculture basket"},
    "livestock": {"COW": "Livestock (cattle and hogs)"},
}


@ttl_cache(900)
def commodity_etf_trend(commodity: str | None = None, days: int = 30) -> dict:
    """Commodity ETFs from Alpaca: one commodity's funds, or one headline fund per commodity."""
    if commodity is not None and commodity not in COMMODITY_ETFS:
        raise ValueError(f"commodity must be one of {', '.join(COMMODITY_ETFS)}")
    funds = (COMMODITY_ETFS[commodity] if commodity
             else {next(iter(f)): c for c, f in COMMODITY_ETFS.items()} | {"DBC": "broad commodities"})
    return {"source": "Alpaca (IEX feed), commodity ETFs", "days": days, "funds": _alpaca_funds(funds, days)}


# --- Global weather and sea-state grids (Open-Meteo, no key) ------------------------------------

GRID_STEP = 10  # degrees. shortcut: Open-Meteo's free tier counts each grid point as a call
                # (10k/day); a 10° grid cached 4 h stays under it. Finer grids need a paid key
                # or our own GFS ingest.
GRID_LATS = list(range(-80, 81, GRID_STEP))
GRID_LONS = list(range(-180, 180, GRID_STEP))
_GRID_CHUNK = 400  # points per request; longer coordinate lists exceed the URL limit


def keep_last(fn):
    """If a refresh fails (rate limit, outage), serve the last good result marked stale."""
    last: dict = {}

    @wraps(fn)
    def inner(*args):
        try:
            last[args] = fn(*args)
            return last[args]
        except (httpx.HTTPError, ValueError):
            if args in last:
                return {**last[args], "stale": True}
            raise

    return inner


def _grid_fetch(url: str, fields: list[str]) -> dict[str, list]:
    lats = [la for la in GRID_LATS for _ in GRID_LONS]
    lons = [lo for _ in GRID_LATS for lo in GRID_LONS]
    out: dict[str, list] = {f: [] for f in fields}
    extra = {"wind_speed_unit": "ms"} if "wind_speed_10m" in fields else {}
    for i in range(0, len(lats), _GRID_CHUNK):
        r = httpx.get(url, params={"latitude": ",".join(map(str, lats[i:i + _GRID_CHUNK])),
                                   "longitude": ",".join(map(str, lons[i:i + _GRID_CHUNK])),
                                   "current": ",".join(fields), **extra},
                      headers={"User-Agent": "nostos-dev research tool"}, timeout=TIMEOUT)
        r.raise_for_status()
        for loc in r.json():
            cur = loc.get("current") or {}
            for f in fields:
                out[f].append(cur.get(f))
    return out


def _uv(speed: list, direction: list) -> tuple[list, list]:
    """Meteorological direction (where the wind comes FROM) -> eastward u, northward v."""
    u, v = [], []
    for s, d in zip(speed, direction):
        if s is None or d is None:
            u.append(None); v.append(None)  # noqa: E702
            continue
        rad = math.radians(d)
        u.append(round(-s * math.sin(rad), 2)); v.append(round(-s * math.cos(rad), 2))  # noqa: E702
    return u, v


@ttl_cache(4 * 3600)
@keep_last
def weather_grid() -> dict:
    """Current temperature (°C), precipitation (mm), cloud cover (%) and 10 m wind (u/v, m/s) on a
    global grid, row-major from the southernmost row, west to east."""
    g = _grid_fetch("https://api.open-meteo.com/v1/forecast",
                    ["temperature_2m", "precipitation", "cloud_cover", "wind_speed_10m", "wind_direction_10m"])
    u, v = _uv(g["wind_speed_10m"], g["wind_direction_10m"])
    return {"source": "Open-Meteo (NOAA GFS / ECMWF blend)", "fetched_at": datetime.now(timezone.utc).isoformat(),
            "step": GRID_STEP, "lats": GRID_LATS, "lons": GRID_LONS,
            "temp_c": g["temperature_2m"], "precip_mm": g["precipitation"], "cloud_pct": g["cloud_cover"],
            "wind_u": u, "wind_v": v, "wind_ms": g["wind_speed_10m"]}


@ttl_cache(4 * 3600)
@keep_last
def marine_grid() -> dict:
    """Current significant wave height (m), direction and period on the same grid; null over land.
    Wave direction is where waves come FROM; wave_u/wave_v point where they travel."""
    g = _grid_fetch("https://marine-api.open-meteo.com/v1/marine", ["wave_height", "wave_direction", "wave_period"])
    u, v = _uv([1.0 if h is not None else None for h in g["wave_height"]], g["wave_direction"])
    return {"source": "Open-Meteo Marine", "fetched_at": datetime.now(timezone.utc).isoformat(),
            "step": GRID_STEP, "lats": GRID_LATS, "lons": GRID_LONS,
            "wave_m": g["wave_height"], "wave_period_s": g["wave_period"], "wave_u": u, "wave_v": v}
