"""Read public CSV/TSV, HTML tables, XLSX, and optional legacy XLS reports."""

import csv
from datetime import datetime, timedelta
from html.parser import HTMLParser
from io import BytesIO, StringIO
import re
from xml.etree import ElementTree as ET
from zipfile import ZipFile

MAX_BYTES = 20 * 1024 * 1024
MAX_EXPANDED = 80 * 1024 * 1024


def clean(value):
    return " ".join(str(value if value is not None else "").replace("\xa0", " ").split())


def key(value):
    return re.sub(r"[^a-z0-9]", "", clean(value).lower())


class HTMLReport(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables, self.links, self.headings = [], [], []
        self.table = self.row = self.cell = self.link = None
        self.heading = None
        self.context = ""
        self.text = []
        self.ignored = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in {"script", "style", "noscript"}:
            self.ignored += 1
        if self.ignored:
            return
        if tag in {"h1", "h2", "h3", "h4"}:
            self.heading = []
        if tag == "table" and self.table is None:
            self.table = {"context": self.context, "rows": []}
        elif tag == "tr" and self.table is not None:
            self.row = []
        elif tag in {"td", "th"} and self.row is not None:
            self.cell = []
        if tag == "a":
            self.link = {"href": attrs.get("href", ""), "text": [], "context": clean(" ".join(self.text[-20:]))}

    def handle_data(self, data):
        if self.ignored:
            return
        value = clean(data)
        if value:
            self.text.append(value)
        if self.heading is not None:
            self.heading.append(data)
        if self.cell is not None:
            self.cell.append(data)
        if self.link is not None:
            self.link["text"].append(data)

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript"}:
            self.ignored = max(0, self.ignored - 1)
            return
        if self.ignored:
            return
        if tag in {"h1", "h2", "h3", "h4"} and self.heading is not None:
            self.context = clean(" ".join(self.heading))
            self.headings.append(self.context)
            self.heading = None
        if tag in {"td", "th"} and self.cell is not None:
            self.row.append(clean(" ".join(self.cell)))
            self.cell = None
        elif tag == "tr" and self.row is not None:
            self.table["rows"].append(self.row)
            self.row = None
        elif tag == "table" and self.table is not None:
            self.tables.append(self.table)
            self.table = None
        if tag == "a" and self.link is not None:
            self.link["text"] = clean(" ".join(self.link["text"]))
            self.links.append(self.link)
            self.link = None


def html_report(text):
    parser = HTMLReport()
    parser.feed(text)
    return parser


def _xlsx(data):
    with ZipFile(BytesIO(data)) as book:
        if sum(info.file_size for info in book.infolist()) > MAX_EXPANDED:
            raise ValueError("Expanded workbook exceeds 80 MB")
        ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        shared = []
        if "xl/sharedStrings.xml" in book.namelist():
            root = ET.fromstring(book.read("xl/sharedStrings.xml"))
            shared = ["".join(si.itertext()) for si in root.findall("s:si", ns)]
        date_styles, percent_styles = set(), set()
        if "xl/styles.xml" in book.namelist():
            styles = ET.fromstring(book.read("xl/styles.xml"))
            custom = {int(n.attrib["numFmtId"]): n.attrib.get("formatCode", "") for n in styles.findall("s:numFmts/s:numFmt", ns)}
            for i, style in enumerate(styles.findall("s:cellXfs/s:xf", ns)):
                fmt = int(style.attrib.get("numFmtId", 0))
                code = custom.get(fmt, "")
                if fmt in {14, 15, 16, 17, 18, 19, 20, 21, 22, 45, 46, 47} or re.search(r"[dy]", re.sub(r'"[^"]*"', "", code), re.I):
                    date_styles.add(i)
                if fmt in {9, 10} or "%" in code:
                    percent_styles.add(i)
        props = ET.fromstring(book.read("xl/workbook.xml")).find("s:workbookPr", ns)
        epoch = datetime(1904, 1, 1) if props is not None and props.attrib.get("date1904") in {"1", "true"} else datetime(1899, 12, 30)
        tables = []
        for name in sorted(book.namelist()):
            if not re.fullmatch(r"xl/worksheets/sheet\d+\.xml", name):
                continue
            rows = []
            for row in ET.fromstring(book.read(name)).findall("s:sheetData/s:row", ns):
                cells = []
                for cell in row.findall("s:c", ns):
                    ref = re.match(r"([A-Z]+)", cell.attrib.get("r", "A1")).group(1)
                    index = 0
                    for char in ref:
                        index = index * 26 + ord(char) - 64
                    if index > 512:
                        raise ValueError("Workbook has too many columns")
                    while len(cells) < index:
                        cells.append("")
                    value = cell.find("s:v", ns)
                    text = value.text if value is not None else ""
                    kind = cell.attrib.get("t")
                    if kind == "s" and text:
                        text = shared[int(text)]
                    elif kind == "inlineStr":
                        text = "".join(cell.find("s:is", ns).itertext())
                    elif kind == "e":
                        text = ""
                    elif text and kind != "str":
                        style = int(cell.attrib.get("s", 0))
                        if style in date_styles:
                            text = (epoch + timedelta(days=float(text))).isoformat()
                        elif style in percent_styles:
                            text = str(float(text) * 100)
                    cells[index - 1] = text or ""
                rows.append(cells)
            tables.append({"context": name, "rows": rows})
        return tables


def read_tables(data, filename="report.csv"):
    if len(data) > MAX_BYTES:
        raise ValueError("Report exceeds 20 MB")
    if data.startswith(b"PK"):
        with ZipFile(BytesIO(data)) as archive:
            if sum(info.file_size for info in archive.infolist()) > MAX_EXPANDED:
                raise ValueError("Expanded archive exceeds 80 MB")
            if "xl/workbook.xml" in archive.namelist():
                return _xlsx(data)
            files = [n for n in archive.namelist() if n.lower().endswith((".csv", ".tsv", ".txt"))]
            if not files:
                raise ValueError("Archive has no CSV/TSV report")
            return [t for name in files for t in read_tables(archive.read(name), name)]
    if data.startswith(b"\xd0\xcf\x11\xe0"):
        try:
            import xlrd
        except ImportError as error:
            raise ValueError("Legacy .xls requires xlrd (python -m pip install xlrd), or save the report as .xlsx/CSV and import it.") from error
        book = xlrd.open_workbook(file_contents=data, formatting_info=True)
        try:
            tables = []
            for sheet in book.sheets():
                rows = []
                for row in sheet.get_rows():
                    values = []
                    for cell in row:
                        if cell.ctype == xlrd.XL_CELL_DATE:
                            value = xlrd.xldate_as_datetime(cell.value, book.datemode).isoformat()
                        elif cell.ctype == xlrd.XL_CELL_NUMBER and "%" in book.format_map[book.xf_list[cell.xf_index].format_key].format_str:
                            value = clean(cell.value * 100)
                        else:
                            value = clean(cell.value)
                        values.append(value)
                    rows.append(values)
                tables.append({"context": sheet.name, "rows": rows})
            return tables
        finally:
            book.release_resources()
    text = data.decode("utf-8-sig" if not data.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-16", errors="replace")
    if re.search(r"<(?:!doctype|html|table)\b", text, re.I):
        return html_report(text).tables
    lines = text.splitlines()
    # Sniff only a likely header onwards, since official reports may have titles.
    start = next((i for i, line in enumerate(lines) if re.search(r"\bISIN\b|Security Description", line, re.I) and re.search(r"[,\t;|]", line)), 0)
    sample = "\n".join(lines[start:start + 10])
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t;|")
    except csv.Error:
        header = lines[start] if lines else ""
        delimiter = max(",\t;|", key=lambda candidate: header.count(candidate))
        return [{"context": filename, "rows": list(csv.reader(StringIO(text), delimiter=delimiter))}]
    return [{"context": filename, "rows": list(csv.reader(StringIO(text), dialect))}]


def records(table, required=("isin",)):
    """Find a real header after optional title rows; tolerate repeated headers."""
    headers = None
    accepted = set(required)
    if "isin" in accepted:
        accepted.update({"isincode", "isinno", "isinnumber", "securityisin"})
    for row in table["rows"]:
        normalized = [key(cell) for cell in row]
        if any(name in accepted for name in normalized):
            headers = normalized
            continue
        if headers is not None:
            yield {name: clean(row[i]) for i, name in enumerate(headers) if name and i < len(row)}
