"""A small, deliberately conservative adapter for Bondbazaar's public page.

No login, private endpoints, browser impersonation, or protection bypasses.
"""

from datetime import datetime, timezone
from html.parser import HTMLParser
import re
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from urllib.robotparser import RobotFileParser

SOURCE = "Bondbazaar"
SOURCE_URL = "https://www.bondbazaar.com/"
USER_AGENT = "BondScope/0.1 (public bond listing research)"
MAX_BYTES = 5 * 1024 * 1024
CATEGORIES = {
    "g-sec": "State government",
    "sgbs": "Sovereign gold",
    "psu": "PSU",
    "nbfc": "Corporate / NBFC",
    "tax-free": "Tax-free",
    "bond etfs": "ETF",
}


def utcnow():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class PageText(HTMLParser):
    """Preserve heading boundaries; ignore script/style contents."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.ignored = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "noscript"}:
            self.ignored += 1
        elif not self.ignored:
            if tag in {"h2", "h3", "h4"}:
                self.parts.append(f"\n[{tag}] ")
            elif tag in {"p", "div", "br", "li", "h6", "section", "span"}:
                self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript"}:
            self.ignored = max(0, self.ignored - 1)
        elif not self.ignored:
            self.parts.append("\n" if tag in {"h2", "h3", "h4"} else " ")

    def handle_data(self, data):
        if not self.ignored:
            self.parts.append(data)


def number(value):
    if value is None:
        return None
    try:
        n = float(value.strip().rstrip("%"))
        return n if 0 <= n <= 100 else None
    except ValueError:
        return None


def money(value):
    """Accept unambiguous plain, Indian, or international grouping only.

    The source uses strings like '1,000,00'; do not guess whether these
    mean 1,000.00 or 100,000. Preserve them as raw text instead.
    """
    if not value:
        return None
    value = re.sub(r"[₹\s]", "", value)
    if not re.fullmatch(r"(?:\d+|\d{1,3}(?:,\d{3})+|\d{1,2}(?:,\d{2})*,\d{3})(?:\.\d{1,2})?", value):
        return None
    return float(value.replace(",", ""))


def field(text, label):
    # A decimal point belongs to a number; a full stop followed by a space
    # separates the compact SEO fields. Both compact and visible cards work.
    labels = r"ISIN|Interest\s+Payment|Sector|Yield|Minimum\s+investment|Min\s+Invest|Coupon\s+Rate|Tenure"
    match = re.search(
        rf"(?:{label})\s*:?\s*(.*?)(?=\.?\s+(?:{labels})\s*:?|$)",
        text, flags=re.I,
    )
    return match.group(1).strip(" .:") if match else None


def parse_bondbazaar(html, observed_at=None):
    parser = PageText()
    parser.feed(html)
    text = "".join(parser.parts)
    segments = re.split(r"\[(h[234])\]\s*", text)
    category = None
    found = {}
    observed_at = observed_at or utcnow()
    for i in range(1, len(segments), 2):
        level, content = segments[i:i + 2]
        title, _, body = content.partition("\n")
        title = " ".join(title.split())
        if level in {"h2", "h3"}:
            category = next((v for k, v in CATEGORIES.items() if k in title.lower()), None)
            continue
        if not category or category == "ETF":
            continue
        body = " ".join(body.split())
        match = re.search(r"\bISIN\s*:\s*(IN[A-Z0-9]{10})\b", body, re.I)
        if not match:
            continue
        isin = match.group(1).upper()
        quoted_yield = number(field(body, "Yield"))
        coupon = number(field(body, r"Coupon\s+Rate"))
        if quoted_yield is None and coupon is None:
            continue
        minimum_raw = field(body, r"Minimum\s+investment|Min\s+Invest")
        found.setdefault(isin, {
            "isin": isin, "issuer": title, "category": category,
            "yield_pct": quoted_yield, "yield_label": "Source-quoted yield",
            "coupon_pct": coupon, "rating": None, "price_inr": None,
            "min_investment_inr": money(minimum_raw),
            "min_investment_raw": minimum_raw,
            "payout": field(body, r"Interest\s+Payment"),
            "tenure_raw": field(body, "Tenure"),
            "maturity_date": None, "quote_at": None,
            "sector": field(body, "Sector"),
            "source": SOURCE, "source_url": SOURCE_URL,
            "observed_at": observed_at,
        })
    if not found:
        raise ValueError("No bond listings parsed. The source layout may have changed; existing data was retained.")
    return list(found.values())


def _get(url, timeout=12):
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,text/plain", "Accept-Encoding": "identity"})
    with urlopen(request, timeout=timeout) as response:
        data = response.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise ValueError("Source response exceeds 5 MB limit")
        return data.decode(response.headers.get_content_charset() or "utf-8", errors="replace")


def fetch_bondbazaar():
    origin = urlparse(SOURCE_URL)
    robots_url = f"{origin.scheme}://{origin.netloc}/robots.txt"
    robots = RobotFileParser()
    try:
        robots.parse(_get(robots_url).splitlines())
    except HTTPError as error:
        error.close()
        if error.code not in {404, 410}:
            raise RuntimeError(f"Cannot verify robots.txt (HTTP {error.code}); fetch skipped") from error
        robots.parse([])
    if not robots.can_fetch(USER_AGENT, SOURCE_URL):
        raise RuntimeError("Source robots.txt disallows this scraper")
    delay = robots.crawl_delay(USER_AGENT) or robots.crawl_delay("*")
    if delay and delay > 300:
        raise RuntimeError(f"Source requests a {delay}s crawl delay; automatic fetching skipped")
    return parse_bondbazaar(_get(SOURCE_URL))
