"""Fetch source documents and readable attachments with bounded downloads."""

import io
import ipaddress
import re
import socket
import zipfile
import zlib
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit
from xml.etree import ElementTree as ET

import httpx
from bs4 import BeautifulSoup

EXTENSIONS = ("pdf", "hwp", "hwpx", "docx", "xlsx")
TRACKING = {"fbclid", "gclid", "mc_cid", "mc_eid"}


def clean_url(value: str) -> str:
    parts = urlsplit(value.strip())
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname or parts.username:
        raise ValueError("유효하지 않은 웹 주소입니다.")
    query = urlencode([(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                       if not k.lower().startswith("utm_") and k.lower() not in TRACKING])
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, query, ""))


def public_url(value: str) -> str:
    url = clean_url(value)
    host = urlsplit(url).hostname
    addresses = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
        raise ValueError("공개 웹 주소만 조사할 수 있습니다.")
    return url


def document_type(url: str, content_type: str = "") -> str:
    name = urlsplit(url).path.lower()
    for extension in EXTENSIONS:
        if name.endswith("." + extension):
            return extension.upper()
    for extension, mime in (("PDF", "application/pdf"), ("HWP", "application/x-hwp"),
                            ("DOCX", "wordprocessingml"), ("XLSX", "spreadsheetml")):
        if mime in content_type.lower():
            return extension
    return "HTML"


def sniff_document(data: bytes, url: str, content_type: str, title: str = "") -> str:
    kind = document_type(url, content_type)
    if kind != "HTML":
        return kind
    kind = document_type(title)
    if kind != "HTML":
        return kind
    if data.startswith(b"%PDF"):
        return "PDF"
    if data.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        return "HWP"
    if data.startswith(b"PK\x03\x04"):
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = archive.namelist()
            if "word/document.xml" in names:
                return "DOCX"
            if "xl/workbook.xml" in names:
                return "XLSX"
            if any(re.search(r"(^|/)section\d+\.xml$", name, re.I) for name in names):
                return "HWPX"
    return "HTML"


def _xml_text(archive: zipfile.ZipFile, paths: list[str], tag: str) -> str:
    lines = []
    for path in paths[:30]:
        if archive.getinfo(path).file_size > 3_000_000:
            raise ValueError("첨부 XML이 너무 큽니다.")
        root = ET.fromstring(archive.read(path))
        line = " ".join((node.text or "").strip() for node in root.iter()
                        if node.tag.rsplit("}", 1)[-1] == tag and node.text)
        if line:
            lines.append(line)
    return "\n".join(lines)[:24000]


def extract_binary(data: bytes, kind: str) -> str:
    if kind == "PDF":
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data))
        return "\n".join(page.extract_text() or "" for page in reader.pages[:30])[:24000]
    if kind == "HWP":
        import olefile
        with olefile.OleFileIO(io.BytesIO(data)) as ole:
            if ole.exists("PrvText"):
                preview = ole.openstream("PrvText").read().decode("utf-16le", errors="ignore")
                if preview.strip():
                    return preview[:24000]
            compressed = bool(ole.openstream("FileHeader").read()[36] & 1)
            chunks = []
            for path in ole.listdir():
                if len(path) == 2 and path[0] == "BodyText" and path[1].startswith("Section"):
                    payload = ole.openstream(path).read()
                    if compressed:
                        payload = zlib.decompress(payload, -15)
                    chunks.extend(re.findall(r"[가-힣A-Za-z0-9 ,.:/%()\-]{8,}", payload.decode("utf-16le", errors="ignore")))
            return "\n".join(chunks)[:24000]
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = archive.namelist()
        if kind == "DOCX":
            return _xml_text(archive, ["word/document.xml"], "t")
        if kind == "HWPX":
            return _xml_text(archive, [n for n in names if re.search(r"(^|/)section\d+\.xml$", n, re.I)], "t")
        if kind == "XLSX":
            shared = []
            if "xl/sharedStrings.xml" in names:
                root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
                shared = ["".join(node.text or "" for node in item.iter() if node.tag.endswith("}t"))
                          for item in root if item.tag.endswith("}si")]
            rows = []
            for name in [n for n in names if re.search(r"xl/worksheets/sheet\d+\.xml$", n)][:6]:
                root = ET.fromstring(archive.read(name))
                for cell in root.iter():
                    if not cell.tag.endswith("}c"):
                        continue
                    values = [node.text for node in cell.iter() if node.tag.endswith(("}v", "}t")) and node.text]
                    if values:
                        value = values[0]
                        if cell.get("t") == "s" and value.isdigit() and int(value) < len(shared):
                            value = shared[int(value)]
                        rows.append(f"{cell.get('r', '')}: {value}")
                    if len(rows) >= 500:
                        break
            return "\n".join(rows)[:24000]
    return ""


def source_rank(url: str, title: str) -> tuple[int, str, bool]:
    host = (urlsplit(url).hostname or "").lower()
    correction = bool(re.search(r"정정|변경공고|수정공고", title))
    notice = bool(re.search(r"공고|모집|고시", title))
    press = bool(re.search(r"보도자료|보도", title))
    if host.endswith(".go.kr") or host == "gov.kr":
        rank, kind = (1, "원발행기관 공식 공고") if notice else ((2, "공식 보도자료") if press else (4, "공공기관 재게시"))
    elif host == "korea.kr" or host.endswith(".korea.kr"):
        rank, kind = 4, "공공기관 재게시"
    elif host.endswith(".or.kr"):
        rank, kind = (3, "공식 시행기관") if notice else (4, "공공기관 재게시")
    else:
        rank, kind = (5, "언론/기타")
    return (0 if correction and rank <= 4 else rank), kind, correction


async def fetch_document(client: httpx.AsyncClient, url: str, title: str = "") -> tuple[dict, list[tuple[str, str]]]:
    """Never treat a search snippet as a fetched source; redirect targets are checked."""
    current = public_url(url)
    for _ in range(5):
        async with client.stream("GET", current, follow_redirects=False) as response:
            if response.status_code in (301, 302, 303, 307, 308):
                current = public_url(urljoin(current, response.headers["location"]))
                continue
            response.raise_for_status()
            limit = 9_000_000 if document_type(current, response.headers.get("content-type", "")) != "HTML" else 2_000_000
            chunks, size = [], 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > limit:
                    raise ValueError("자료 크기가 분석 제한을 초과합니다.")
                chunks.append(chunk)
            payload = b"".join(chunks)
            kind = sniff_document(payload, current, response.headers.get("content-type", ""),
                                  title + " " + response.headers.get("content-disposition", ""))
            links, published_at = [], None
            if kind == "HTML":
                soup = BeautifulSoup(payload, "html.parser")
                for node in soup(["script", "style", "nav", "footer"]):
                    node.decompose()
                if not title or title == "입력 URL":
                    meta_title = soup.find("meta", attrs={"property": "og:title"})
                    title = (soup.title.get_text(" ", strip=True) if soup.title else "") or (
                        meta_title.get("content", "") if meta_title else "")
                meta = soup.find("meta", attrs={"property": "article:published_time"})
                published_at = meta.get("content") if meta else None
                if not published_at:
                    date = re.search(r"20\d{2}[.\-/]\s?\d{1,2}[.\-/]\s?\d{1,2}", soup.get_text(" ", strip=True)[:2500])
                    published_at = date.group() if date else None
                for anchor in soup.find_all("a", href=True):
                    label = anchor.get_text(" ", strip=True)
                    target = urljoin(current, anchor["href"])
                    if re.search(r"\.(?:pdf|hwp|hwpx|docx|xlsx)(?:$|[?#\s])", target + " " + label, re.I):
                        links.append((target, label))
                text = soup.get_text(" ", strip=True)[:24000]
            else:
                text = extract_binary(payload, kind)
            rank, source_type, correction = source_rank(current, title)
            return ({"url": current, "title": title, "source_type": source_type, "source_rank": rank,
                     "is_correction": correction, "document_type": kind, "published_at": published_at,
                     "extract_status": "OK" if text.strip() else "UNREADABLE", "excerpt": text}, links)
    raise ValueError("주소 이동이 너무 많습니다.")
