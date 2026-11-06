"""Normalized observations; prices and yields retain their original basis."""

from datetime import date, datetime
import hashlib
import math
import re
from zoneinfo import ZoneInfo

from .scraper import utcnow
from .tabular import clean, key

YIELD_LABELS = {
    "quoted": "Source-quoted yield",
    "traded_annualized": "Last traded yield (NSE annualized)",
    "traded_ytm": "Last traded YTM",
    "weighted_ytm": "Weighted average traded YTM",
    "weighted_yield": "Weighted average traded yield (source basis)",
    "ccil_traded": "Last traded yield (CCIL basis)",
    "indicative": "Indicative yield at face value",
    "reference": "Reference data (no yield)",
}


def pick(row, *names):
    for name in names:
        value = row.get(key(name))
        if value and value.lower() not in {"-", "--", "na", "n/a", "null", "none"}:
            return value
    return None


def numeric(value, percent=False):
    if value is None:
        return None
    text = clean(value).replace("₹", "").rstrip("%").strip()
    if "," in text and not re.fullmatch(r"(?:\d{1,3}(?:,\d{3})+|\d{1,2}(?:,\d{2})*,\d{3})(?:\.\d+)?", text):
        return None
    text = text.replace(",", "")
    try:
        result = float(text)
        return result if math.isfinite(result) and (0 <= result <= 100 if percent else result >= 0) else None
    except ValueError:
        return None


def parse_date(value):
    if not value:
        return None
    text = clean(value).replace("Sept", "Sep")
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        pass
    for fmt in ("%d-%b-%Y", "%d %b %Y", "%d %B %Y", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d%m%Y"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def isin_value(value):
    text = clean(value).upper()
    match = re.search(r"\bIN[A-Z0-9]{10}\b", text)
    return match.group() if match else None


def observation(source_id, source, source_url, *, isin=None, issuer=None,
                category="Corporate / NBFC", yield_kind="reference", **fields):
    bond = dict(isin=isin, issuer=issuer or isin or "Unnamed security", category=category,
                yield_pct=None, yield_kind=yield_kind, yield_label=YIELD_LABELS[yield_kind],
                coupon_pct=None, rating=None, price_inr=None, price_pct_face=None,
                price_basis=None, face_value_inr=None, min_investment_inr=None,
                min_investment_raw=None, payout=None, tenure_raw=None, maturity_date=None,
                quote_at=None, report_as_of=None, sector=None, instrument_status="unknown",
                source_id=source_id, source=source, source_url=source_url, observed_at=utcnow())
    bond.update(fields)
    # ISIN-less CCIL observations get a source-scoped identity, never a fake ISIN.
    identity = isin or f"{source_id}:{bond['issuer']}:{bond['maturity_date'] or ''}"
    bond["instrument_id"] = identity
    digest = hashlib.sha256(f"{source_id}|{identity}|{yield_kind}".encode()).hexdigest()[:24]
    bond["id"] = digest
    bond["field_sources"] = {}
    return bond


def normalize_legacy(bond):
    fields = {k: v for k, v in bond.items() if k not in {"source", "source_url", "isin", "issuer", "category", "yield_kind", "source_id"}}
    return observation(bond.get("source_id", "bondbazaar"), bond["source"], bond["source_url"],
                       isin=bond.get("isin"), issuer=bond.get("issuer"), category=bond.get("category", "Corporate / NBFC"),
                       yield_kind=bond.get("yield_kind", "quoted"), **fields)


def effective_status(bond):
    if bond.get("instrument_status") in {"redeemed", "defaulted"}:
        return bond["instrument_status"]
    maturity = parse_date(bond.get("maturity_date"))
    if maturity and maturity <= datetime.now(ZoneInfo("Asia/Kolkata")).date().isoformat():
        return "matured"
    return bond.get("instrument_status", "unknown")
