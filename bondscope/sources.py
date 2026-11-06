"""Public source adapters and local-report import paths.

Only published links are followed. Dynamic pages with no records fail visibly;
they are never treated as empty successful market snapshots.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
import re
import threading
import time
from urllib.error import HTTPError
from urllib.parse import unquote, urljoin, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener
from urllib.robotparser import RobotFileParser

from .models import isin_value, numeric, observation, parse_date, pick
from .scraper import USER_AGENT, fetch_bondbazaar, parse_bondbazaar, utcnow
from .tabular import MAX_BYTES, clean, html_report, key, read_tables, records

NSE_URL = "https://www.nseindia.com/all-reports-debt"
NSE_TRADES = "https://www.nseindia.com/market-data/debt-market-reporting-corporate-bonds-traded-on-exchange"
NSDL_URL = "https://nsdl.com/resources/data/detailed-list-debt-instruments"
CDSL_URL = "https://www.cdslindia.com/CorporateBond/SearchISIN.aspx"
CCIL_URL = "https://www.ccilindia.com/en/web/ccil/market-watch"
GRIP_URL = "https://www.gripinvest.in/bond-directory/bonds"


def report_date(text):
    decoded = unquote(text)
    for pattern in (r"(?<!\d)\d{4}-\d{2}-\d{2}(?!\d)", r"(?<!\d)\d{2}[.\-/]\d{2}[.\-/]\d{4}(?!\d)", r"(?<!\d)\d{2}[- ](?:[A-Za-z]{3,9})[- ]\d{4}(?!\d)", r"(?<!\d)\d{8}(?!\d)"):
        for match in re.finditer(pattern, decoded):
            value = parse_date(match.group())
            if value:
                return value
    return None


def _quote_date(value):
    if not value:
        return None
    value = clean(value)
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return parse_date(value)
    # Preserve published time with the Indian market timezone; a date alone
    # stays a date rather than acquiring a fabricated midnight trade time.
    for fmt in ("%d-%b-%Y %H:%M:%S", "%d-%m-%Y %H:%M:%S", "%d/%m/%Y %H:%M:%S"):
        try:
            return datetime.strptime(value, fmt).isoformat() + "+05:30"
        except ValueError:
            pass
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).isoformat() + ("+05:30" if len(value) > 10 and not re.search(r"[+-]\d\d:\d\d$|Z$", value) else "")
    except ValueError:
        return parse_date(value)


def _category(text):
    text = (text or "").lower()
    if "gold" in text or re.search(r"\bsgb\b", text):
        return "Sovereign gold"
    if "tax-free" in text or "tax free" in text:
        return "Tax-free"
    if "state" in text or re.search(r"\bsdl\b|\bsgs\b", text):
        return "State government"
    if "t-bill" in text or "treasury" in text:
        return "Treasury bill"
    if "central government" in text or "government of india" in text or re.search(r"\bgoi\b|\bg-sec\b", text):
        return "Central government"
    return "PSU" if "psu" in text else "Corporate / NBFC"


def _status(value):
    value = (value or "").lower()
    if "default" in value:
        return "defaulted"
    if any(word in value for word in ("redeemed", "redemption completed", "extinguished")):
        return "redeemed"
    if value in {"active", "outstanding", "admitted"}:
        return "outstanding"
    return "unknown"


def parse_nse(data, filename="nse.csv", as_of=None):
    result = []
    as_of = as_of or report_date(filename)
    for table in read_tables(data, filename):
        for row in records(table):
            isin = isin_value(pick(row, "ISIN", "ISIN code", "symbol"))
            if not isin:
                continue
            issuer = pick(row, "issuer", "issuer name", "company name", "security name", "security description", "descriptor", "description")
            coupon = numeric(pick(row, "coupon rate", "coupon", "coupon rate (%)"), percent=True)
            # YTM is taken only from an explicitly named field. Generic 'yield'
            # is not promoted to YTM. Weighted-average and last-trade stay apart.
            metrics = [
                ("traded_annualized", pick(row, "Last Trade Yield (Annualized) (%)", "last trade yield", "last traded yield")),
                ("traded_ytm", pick(row, "last traded YTM", "last trade YTM")),
                ("weighted_ytm", pick(row, "Weighted Average Yield(YTM) (%)", "weighted average YTM")),
                ("weighted_yield", pick(row, "weighted average yield", "WAVG YIELD")),
            ]
            metrics = [(kind, numeric(value, percent=True)) for kind, value in metrics if numeric(value, percent=True) is not None] or [("reference", None)]
            for kind, value in metrics:
                # CBM trades quote prices as a percentage of face value. An
                # explicitly named rupee price is the only direct INR value.
                price = numeric(pick(row, "weighted average price", "WAP") if kind in {"weighted_ytm", "weighted_yield"} else pick(row, "last trade price", "last traded price"))
                result.append(observation("nse", "NSE", NSE_URL, isin=isin, issuer=issuer,
                    category=_category(pick(row, "category", "security type", "instrument type") or issuer),
                    yield_kind=kind, yield_pct=value, coupon_pct=coupon,
                    rating=pick(row, "credit rating", "rating"),
                    price_pct_face=price, price_basis="percent_face" if price is not None else None,
                    price_inr=numeric(pick(row, "price INR", "price rupees")),
                    face_value_inr=numeric(pick(row, "face value", "facevalue")),
                    maturity_date=parse_date(pick(row, "maturity date", "maturitydate", "redemption date")),
                    quote_at=_quote_date(pick(row, "last trade time", "trade date", "date", "timestamp")) or as_of,
                    report_as_of=as_of, instrument_status=_status(pick(row, "status", "instrument status")),
                    trade_count=numeric(pick(row, "No. of Trades", "number of trades")),
                    traded_value_raw=pick(row, "Value(₹ Crores)", "traded value", "total traded value")))
    if not result:
        raise ValueError("No NSE ISIN records found. Download a CBM security master or security-wise trade CSV and use import-report.")
    return result


def parse_reference(data, filename="reference.csv", as_of=None, source_id="nsdl", status=None):
    source, url = ("CDSL", CDSL_URL) if source_id == "cdsl" else ("NSDL", NSDL_URL)
    decoded = unquote(filename).lower()
    status = status or ("defaulted" if "default" in decoded else "redeemed" if "redeemed" in decoded and "entire" not in decoded else None)
    result = []
    for table in read_tables(data, filename):
        for row in records(table):
            isin = isin_value(pick(row, "ISIN", "ISIN code", "ISIN no", "ISIN number"))
            if not isin:
                continue
            issuer = pick(row, "issuer name", "company name", "name of issuer", "name of the issuer", "issuer", "security name", "name of company")
            result.append(observation(source_id, source, url, isin=isin, issuer=issuer,
                category=_category(pick(row, "instrument type", "type of instrument", "category") or issuer),
                coupon_pct=numeric(pick(row, "coupon rate", "coupon rate (%)", "interest rate", "coupon"), percent=True),
                rating=pick(row, "credit rating", "rating"),
                maturity_date=parse_date(pick(row, "maturity date", "date of maturity", "redemption date")),
                face_value_inr=numeric(pick(row, "face value", "face value (Rs.)")),
                payout=pick(row, "interest payment frequency", "interest frequency", "coupon frequency"),
                report_as_of=as_of or report_date(filename),
                instrument_status=status or _status(pick(row, "status", "ISIN status", "redemption status", "admission status"))))
    if not result:
        raise ValueError(f"No {source} ISIN records found; the report layout may have changed.")
    return result


def parse_ccil(data, filename="ccil.html", as_of=None):
    result = []
    for table in read_tables(data, filename):
        for row in records(table, required=("securitydescription", "isin")):
            name = pick(row, "security description", "security name", "description")
            isin = isin_value(pick(row, "ISIN", "ISIN code"))
            if not name and not isin:
                continue
            last_yield = numeric(pick(row, "LTY", "last traded yield", "last trade yield"), percent=True)
            last_price = numeric(pick(row, "LTP", "last traded price", "last trade price"))
            if last_yield is None and last_price is None:
                continue
            result.append(observation("ccil", "CCIL / NDS-OM", CCIL_URL, isin=isin, issuer=name,
                category=_category(pick(row, "category") or table["context"]),
                yield_kind="ccil_traded" if last_yield is not None else "reference", yield_pct=last_yield,
                price_pct_face=last_price, price_basis="percent_face" if last_price is not None else None,
                maturity_date=parse_date(pick(row, "maturity date", "maturity")),
                bid_yield_pct=numeric(pick(row, "bid yield"), percent=True),
                offer_yield_pct=numeric(pick(row, "offer yield"), percent=True),
                bid_price_pct_face=numeric(pick(row, "bid price")),
                offer_price_pct_face=numeric(pick(row, "offer price")),
                quote_at=_quote_date(pick(row, "last trade time", "timestamp", "as on date")) or as_of,
                report_as_of=as_of))
    if not result:
        raise ValueError("CCIL returned no populated market rows (the page may load dynamically). Import a downloaded market-watch CSV/HTML report with --as-of.")
    return result


def parse_grip(data, filename="grip.html", as_of=None):
    text = data.decode("utf-8-sig")
    doc = html_report(text)
    result = {}
    for table in doc.tables:
        # Public directory columns: issuer (including rating/ISIN), coupon,
        # indicative yield plus payout, and maturity. No inferred market price.
        rows = table["rows"]
        header = next((i for i, row in enumerate(rows) if any("indicativeyield" in key(c) for c in row) and any(key(c) == "issuer" for c in row)), None)
        if header is None:
            continue
        for row in rows[header + 1:]:
            if len(row) < 4:
                continue
            isin = isin_value(row[0])
            if not isin:
                continue
            issuer = row[0].split(isin)[0].strip()
            rating = re.search(r"\b(AAA|AA[+-]?|A[+-]?|BBB[+-]?|BB[+-]?|B[+-]?|CCC|CC|C|D|Unrated)\s*$", issuer)
            if rating:
                issuer = issuer[:rating.start()].strip()
            # Link text gives the clean issuer without decorative initials.
            link = next((link for link in doc.links if isin.lower() in link["href"].lower()), None)
            if link:
                issuer = link["text"]
            coupon = re.search(r"(\d+(?:\.\d+)?)\s*%", row[1])
            y = re.search(r"(\d+(?:\.\d+)?)\s*%", row[2])
            maturity = re.search(r"\d{1,2}\s+[A-Za-z]+\s+\d{4}", row[3])
            payout = re.search(r"Semi[- ]?annual|Half[- ]?yearly|Annual|Quarterly|Monthly", row[2], re.I)
            result[isin] = observation("grip", "Grip Invest", urljoin(GRIP_URL, link["href"]) if link else GRIP_URL,
                isin=isin, issuer=issuer, yield_kind="indicative",
                coupon_pct=numeric(coupon.group(1), percent=True) if coupon else None,
                yield_pct=numeric(y.group(1), percent=True) if y else None,
                rating=rating.group(1) if rating else None,
                maturity_date=parse_date(maturity.group()) if maturity else None,
                payout=payout.group() if payout else None, report_as_of=as_of,
                availability="Directory entry; platform availability unverified")
    if not result:
        raise ValueError("No Grip directory table records found. Layout may have changed; previous data retained.")
    return list(result.values())


class PublicClient:
    """Bounded, identified, robots-aware requests to each source's own hosts."""

    def __init__(self, hosts):
        self.hosts, self.robots, self.last_request = set(hosts), {}, {}
        self.robots_checked = {}
        self.lock = threading.RLock()
        client = self
        class Redirects(HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                client.validate(newurl)
                return super().redirect_request(req, fp, code, msg, headers, newurl)
        self.opener = build_opener(Redirects())

    def validate(self, url):
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.hostname not in self.hosts or parsed.username or parsed.password or parsed.port not in {None, 443}:
            raise ValueError("Source link or redirect is outside the allowed HTTPS source hosts")

    def _read(self, url):
        self.validate(url)
        with self.opener.open(Request(url, headers={"User-Agent": USER_AGENT, "Accept-Encoding": "identity"}), timeout=12) as response:
            data = response.read(MAX_BYTES + 1)
            if len(data) > MAX_BYTES:
                raise ValueError("Source report exceeds 20 MB")
            return data

    def get(self, url):
        with self.lock:
            return self._get_checked(url)

    def _get_checked(self, url):
        self.validate(url)
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin not in self.robots or time.monotonic() - self.robots_checked[origin] >= 300:
            rules = RobotFileParser()
            try:
                rules.parse(self._read(origin + "/robots.txt").decode("utf-8").splitlines())
            except HTTPError as error:
                error.close()
                if error.code not in {404, 410}:
                    raise ValueError(f"Cannot verify source robots.txt (HTTP {error.code})") from error
                rules.parse([])
            self.robots[origin] = rules
            self.robots_checked[origin] = time.monotonic()
        rules = self.robots[origin]
        if not rules.can_fetch(USER_AGENT, url):
            raise ValueError("Source robots.txt disallows this path")
        delay = rules.crawl_delay(USER_AGENT) or rules.crawl_delay("*") or 0
        elapsed = time.monotonic() - self.last_request.get(origin, float("-inf"))
        if elapsed < delay:
            raise ValueError(f"Source crawl delay is {delay}s; retry later or import the downloaded report")
        self.last_request[origin] = time.monotonic()
        return self._read(url)


def _fetch_nse():
    client = PublicClient({"www.nseindia.com", "nsearchives.nseindia.com", "archives.nseindia.com"})
    page = client.get(NSE_URL)
    doc = html_report(page.decode("utf-8", errors="replace"))
    links = [l for l in doc.links if re.search(r"\.(?:csv|zip)(?:\?|$)", l["href"], re.I) and re.search(r"corporate_bond_report|cbm.*(?:bhav|trade)|security.*master", unquote(l["href"]) + l["text"], re.I)]
    if links:
        return [bond for link in links[:2] for bond in parse_nse(client.get(urljoin(NSE_URL, link["href"])), link["href"])]
    # Public downloads are frequently populated by JavaScript. Read server-
    # rendered trade rows if present; otherwise give an actionable import error.
    data = client.get(NSE_TRADES)
    return parse_nse(data, "nse.html", _page_as_of(data))


def _page_as_of(data):
    text = " ".join(html_report(data.decode("utf-8", errors="replace")).text)
    match = re.search(r"As (?:on|of)(?: date)?\s*:?\s*(.{8,35})", text, re.I)
    return report_date(match.group(1)) if match else None


_NSDL_CLIENT = PublicClient({"nsdl.com", "www.nsdl.com", "nsdl.co.in", "www.nsdl.co.in"})


def _fetch_nsdl(kind="catalog"):
    # All three NSDL feeds share robots checks and origin request pacing.
    client = _NSDL_CLIENT
    data = client.get(NSDL_URL)
    links = html_report(data.decode("utf-8", errors="replace")).links
    for link in links:
        url = urljoin(NSDL_URL, link["href"])
        name = unquote(url).lower()
        match = ("entire" in name and "debt_instruments" in name) if kind == "catalog" else ("defaulted" in name if kind == "defaults" else "debt_redeemed" in name)
        if match and re.search(r"\.(?:xlsx?|csv)(?:\?|$)", name):
            source_id = {"catalog": "nsdl", "defaults": "nsdl-defaults", "redeemed": "nsdl-redeemed"}[kind]
            return parse_reference(client.get(url), url, source_id=source_id)
    raise ValueError("NSDL has no recognized published report link. Download the debt report and use import-report.")


def _fetch_cdsl():
    client = PublicClient({"www.cdslindia.com", "cdslindia.com"})
    return parse_reference(client.get(CDSL_URL), "cdsl.html", source_id="cdsl")


@dataclass(frozen=True)
class Source:
    id: str
    name: str
    url: str
    role: str
    fetch: object
    parse: object
    note: str = ""


SOURCES = {
    "bondbazaar": Source("bondbazaar", "Bondbazaar", "https://www.bondbazaar.com/", "retail", fetch_bondbazaar,
        lambda data, filename, as_of: parse_bondbazaar(data.decode("utf-8-sig")), "Homepage sample; quote dates unknown."),
    "nse": Source("nse", "NSE", NSE_URL, "trades", _fetch_nse, parse_nse, "Published CSV/ZIP reports or populated HTML tables; dynamic pages may require report import."),
    "nsdl": Source("nsdl", "NSDL", NSDL_URL, "reference", _fetch_nsdl, parse_reference, "Debt instrument catalogue. Legacy XLS requires optional xlrd or CSV/XLSX conversion."),
    "nsdl-redeemed": Source("nsdl-redeemed", "NSDL redeemed", NSDL_URL, "reference", lambda: _fetch_nsdl("redeemed"),
        lambda data, filename, as_of: parse_reference(data, filename, as_of, "nsdl-redeemed", "redeemed")),
    "nsdl-defaults": Source("nsdl-defaults", "NSDL redemption defaults", NSDL_URL, "reference", lambda: _fetch_nsdl("defaults"),
        lambda data, filename, as_of: parse_reference(data, filename, as_of, "nsdl-defaults", "defaulted")),
    "cdsl": Source("cdsl", "CDSL", CDSL_URL, "reference", _fetch_cdsl,
        lambda data, filename, as_of: parse_reference(data, filename, as_of, "cdsl"), "Dynamic search results may require a downloaded CSV/XLSX report."),
    "ccil": Source("ccil", "CCIL / NDS-OM", CCIL_URL, "trades",
        lambda: parse_ccil((data := PublicClient({"www.ccilindia.com", "ccilindia.com"}).get(CCIL_URL)), "ccil.html", _page_as_of(data)), parse_ccil,
        "Only populated tables are ingested. Securities without ISIN keep a CCIL-scoped identity."),
    "grip": Source("grip", "Grip Invest", GRIP_URL, "directory",
        lambda: parse_grip(PublicClient({"www.gripinvest.in", "gripinvest.in"}).get(GRIP_URL)), parse_grip,
        "First public directory page only. Indicative yields are at face value, not executable offers."),
}
