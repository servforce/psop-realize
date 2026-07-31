from __future__ import annotations

import argparse
import json
import random
import re
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

import httpx
from bs4 import BeautifulSoup


DEFAULT_SOURCE_URL = "https://openstd.samr.gov.cn/bzgk/std/"
BASE_URL = "https://openstd.samr.gov.cn"
DEFAULT_ALLOWED_STATUSES = ("现行", "即将实施")
OPENSTD_SCOPES = {
    "mandatory_national": {
        "label": "强制性国家标准",
        "url": "https://openstd.samr.gov.cn/bzgk/std/std_list_type?p.p1=1&p.p90=circulation_date&p.p91=desc",
    },
    "recommended_national": {
        "label": "推荐性国家标准",
        "url": "https://openstd.samr.gov.cn/bzgk/std/std_list_type?p.p1=2&p.p90=circulation_date&p.p91=desc",
    },
    "gbz_guidance": {
        "label": "指导性技术文件",
        "url": "https://openstd.samr.gov.cn/bzgk/std/std_list_type?p.p1=3&p.p90=circulation_date&p.p91=desc",
    },
}
ALL_NATIONAL_SCOPE = "all_national_standards"


@dataclass
class DiscoveredStandard:
    source_scope: str
    source_label: str
    source_url: str
    standard_code: str
    standard_name: str
    standard_status: str
    publish_date: str
    is_adopted: bool
    detail_url: str


@dataclass
class DetailInspection:
    standard_code: str
    standard_name: str
    detail_url: str
    downloadable: bool
    download_url: str
    download_selector: str
    reason: str


def json_dump(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def slugify_standard_code(value: str) -> str:
    slug = value.lower().replace("/", "")
    slug = re.sub(r"[^a-z0-9]+", "_", slug)
    return slug.strip("_") or f"openstd_{uuid.uuid4().hex[:8]}"


def absolutize(url: str, base_url: str = BASE_URL) -> str:
    if not url:
        return ""
    return urljoin(base_url, url)


def build_page_url(source_url: str, page: int, *, page_size: int = 10) -> str:
    parsed = urlparse(source_url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    if page > 1:
        query["r"] = str(random.random())
        query["page"] = str(page)
        query["pageSize"] = str(page_size)
    else:
        query.pop("page", None)
        query.pop("pageSize", None)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, urlencode(query), parsed.fragment))


class OpenStdHttpClient:
    def __init__(self, *, timeout_seconds: float = 180.0) -> None:
        self.client = httpx.Client(
            timeout=timeout_seconds,
            follow_redirects=True,
            trust_env=False,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7",
            },
        )

    def close(self) -> None:
        self.client.close()

    def get_html(self, url: str, *, referer: str = "") -> tuple[str, str]:
        headers = {"Referer": referer} if referer else {}
        response = self.client.get(url, headers=headers)
        response.raise_for_status()
        return response.text, str(response.url)

    def download_pdf(self, *, hcno: str, referer: str, output_path: Path) -> tuple[str, int]:
        show_url = absolutize(f"/bzgk/std/showGb?type=download&hcno={hcno}&request_locale=zh")
        view_url = absolutize(f"/bzgk/std/viewGb?hcno={hcno}")
        show_response = self.client.get(show_url, headers={"Referer": referer})
        show_response.raise_for_status()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        size_bytes = 0
        with self.client.stream("GET", view_url, headers={"Referer": show_url}) as response:
            if response.status_code in {403, 429}:
                return f"http_{response.status_code}", 0
            response.raise_for_status()
            with output_path.open("wb") as file:
                for chunk in response.iter_bytes(1024 * 1024):
                    if not chunk:
                        continue
                    file.write(chunk)
                    size_bytes += len(chunk)
        return "downloaded", size_bytes


def discover(
    url: str,
    *,
    scope: str,
    max_pages: int,
    max_items: int,
    interval_seconds: float,
    allowed_statuses: set[str],
) -> dict[str, Any]:
    client = OpenStdHttpClient()
    items: list[DiscoveredStandard] = []
    total_pages = 0
    pages_processed = 0
    try:
        for source in resolve_sources(scope, url):
            page = 1
            source_pages_processed = 0
            while True:
                page_url = build_page_url(source["url"], page)
                html, final_url = client.get_html(page_url, referer=source["url"] if page > 1 else "")
                source_total_pages = parse_total_pages(html)
                if page == 1:
                    total_pages += source_total_pages
                page_items = parse_list_items(
                    html,
                    final_url,
                    source_scope=source["scope"],
                    source_label=source["label"],
                    source_url=source["url"],
                    allowed_statuses=allowed_statuses,
                )
                for item in page_items:
                    items.append(item)
                    if max_items > 0 and len(items) >= max_items:
                        break
                pages_processed += 1
                source_pages_processed += 1
                if max_items > 0 and len(items) >= max_items:
                    break
                if max_pages > 0 and source_pages_processed >= max_pages:
                    break
                if source_total_pages and page >= source_total_pages:
                    break
                if not page_items and not source_total_pages:
                    break
                page += 1
                time.sleep(max(0.0, interval_seconds))
            if max_items > 0 and len(items) >= max_items:
                break
            time.sleep(max(0.0, interval_seconds))
    finally:
        client.close()
    return {
        "status": "success",
        "source_url": url,
        "scope": scope,
        "pages_processed": pages_processed,
        "total_pages": total_pages,
        "total_seen": len(items),
        "items": [asdict(item) for item in items],
    }


def resolve_sources(scope: str, url: str) -> list[dict[str, str]]:
    normalized = (scope or "").strip() or ALL_NATIONAL_SCOPE
    if normalized == ALL_NATIONAL_SCOPE:
        return [
            {"scope": key, "label": value["label"], "url": value["url"]}
            for key, value in OPENSTD_SCOPES.items()
        ]
    if normalized in OPENSTD_SCOPES:
        value = OPENSTD_SCOPES[normalized]
        return [{"scope": normalized, "label": value["label"], "url": value["url"]}]
    return [{"scope": normalized or "custom", "label": normalized or "自定义范围", "url": url}]


def parse_total_pages(html: str) -> int:
    match = re.search(r"pages\s*:\s*[\"']?(\d+)[\"']?", html)
    if match:
        return int(match.group(1))
    text = clean_text(BeautifulSoup(html, "html.parser").get_text(" ", strip=True))
    match = re.search(r"(\d+)\s*/\s*(\d+)", text)
    if match:
        return int(match.group(2))
    return 0


def parse_list_items(
    html: str,
    page_url: str,
    *,
    source_scope: str = "",
    source_label: str = "",
    source_url: str = "",
    allowed_statuses: set[str] | None = None,
) -> list[DiscoveredStandard]:
    soup = BeautifulSoup(html, "html.parser")
    rows = soup.select("table tbody tr") or soup.select("table tr")
    items: list[DiscoveredStandard] = []
    for row in rows:
        cells = row.find_all(["td", "th"])
        if len(cells) < 6:
            continue
        code = clean_text(cells[1].get_text(" ", strip=True))
        if not is_national_standard_code(code):
            continue
        status = clean_text(cells[4].get_text(" ", strip=True))
        if not is_allowed_status(status, allowed_statuses or set(DEFAULT_ALLOWED_STATUSES)):
            continue
        hcno = extract_hcno(row)
        detail_url = absolutize(f"/bzgk/std/newGbInfo?hcno={hcno}", page_url) if hcno else extract_detail_url(row, page_url)
        items.append(
            DiscoveredStandard(
                source_scope=source_scope,
                source_label=source_label,
                source_url=source_url or page_url,
                standard_code=code,
                standard_name=clean_text(cells[3].get_text(" ", strip=True)),
                standard_status=status,
                publish_date=clean_text(cells[5].get_text(" ", strip=True)).replace(" 00:00:00.0", ""),
                is_adopted=bool(clean_text(cells[2].get_text(" ", strip=True))),
                detail_url=detail_url,
            )
        )
    return items


def is_national_standard_code(code: str) -> bool:
    return bool(re.search(r"\bGB\s*(?:/\s*[TZ])?\s+[0-9]", code, re.I))


def is_allowed_status(status: str, allowed_statuses: set[str]) -> bool:
    if "废止" in status:
        return False
    return not allowed_statuses or status in allowed_statuses


def extract_hcno(node: Any) -> str:
    blob = " ".join(
        filter(
            None,
            [tag.get("onclick") for tag in node.find_all(["a", "button"])]
            + [node.get("onclick") if hasattr(node, "get") else ""],
        )
    )
    match = re.search(r"showInfo\(['\"]([^'\"]+)['\"]\)", blob)
    if match:
        return match.group(1)
    match = re.search(r"hcno=([0-9A-Fa-f]+)", blob)
    if match:
        return match.group(1)
    return ""


def extract_detail_url(row: Any, page_url: str) -> str:
    for link in row.find_all("a"):
        href = link.get("href") or ""
        if href and href != "javascript:void(0)":
            return absolutize(href, page_url)
    return ""


def inspect_detail(detail_url: str, *, timeout_seconds: float = 180.0) -> dict[str, Any]:
    client = OpenStdHttpClient(timeout_seconds=timeout_seconds)
    try:
        html, final_url = client.get_html(detail_url)
        inspection = parse_detail(html, final_url)
        return {"status": "success", "detail": asdict(inspection)}
    finally:
        client.close()


def parse_detail(html: str, detail_url: str) -> DetailInspection:
    soup = BeautifulSoup(html, "html.parser")
    text = clean_text(soup.get_text(" ", strip=True))
    code_match = re.search(r"标准号[:：]\s*(GB\s*(?:/\s*[TZ])?\s+[0-9][0-9A-Za-z./\- ]*)", text, re.I)
    if not code_match:
        code_match = re.search(r"(GB\s*(?:/\s*[TZ])?\s+[0-9][0-9A-Za-z./\- ]*)", text, re.I)
    standard_code = clean_text(code_match.group(1)) if code_match else ""
    standard_name = extract_labeled_value(text, "中文标准名称") or extract_labeled_value(text, "标准名称")
    download_button = soup.select_one(".xz_btn[data-value]")
    hcno = download_button.get("data-value", "").strip() if download_button else extract_hcno(soup)
    if hcno:
        return DetailInspection(
            standard_code=standard_code,
            standard_name=standard_name,
            detail_url=detail_url,
            downloadable=True,
            download_url=absolutize(f"/bzgk/std/viewGb?hcno={hcno}", detail_url),
            download_selector=".xz_btn[data-value]",
            reason="download_button_found",
        )
    return DetailInspection(
        standard_code=standard_code,
        standard_name=standard_name,
        detail_url=detail_url,
        downloadable=False,
        download_url="",
        download_selector="",
        reason="no_download_button",
    )


def extract_labeled_value(text: str, label: str) -> str:
    match = re.search(
        rf"{re.escape(label)}[:：]\s*(.*?)(?=\s+(?:英文标准名称|标准状态|在线预览|下载标准|实施信息反馈|中国标准分类号|国际标准分类号|发布日期|主管部门|归口部门|发布单位|备注)[:：]?|$)",
        text,
    )
    return clean_text(match.group(1)) if match else ""


def download(detail_url: str, *, output_dir: Path, timeout_seconds: float) -> dict[str, Any]:
    client = OpenStdHttpClient(timeout_seconds=timeout_seconds)
    try:
        html, final_url = client.get_html(detail_url)
        inspection = parse_detail(html, final_url)
        if not inspection.downloadable:
            return {"status": "skipped", "reason": inspection.reason or "no_download_button", "detail": asdict(inspection)}
        hcno = parse_hcno_from_url(inspection.download_url) or parse_hcno_from_url(final_url)
        if not hcno:
            return {"status": "skipped", "reason": "hcno_not_found", "detail": asdict(inspection)}
        target_path = output_dir / f"{slugify_standard_code(inspection.standard_code or hcno)}.pdf"
        status, size_bytes = client.download_pdf(hcno=hcno, referer=final_url, output_path=target_path)
        if status != "downloaded":
            target_path.unlink(missing_ok=True)
            return {"status": "failed", "reason": status, "detail": asdict(inspection)}
        valid, reason = validate_pdf(target_path)
        if not valid:
            target_path.unlink(missing_ok=True)
            return {"status": "failed", "reason": reason, "detail": asdict(inspection)}
        return {
            "status": "downloaded",
            "pdf_path": str(target_path),
            "size_bytes": size_bytes,
            "download_method": "showGb_viewGb",
            "detail": asdict(inspection),
        }
    finally:
        client.close()


def parse_hcno_from_url(url: str) -> str:
    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    return query.get("hcno", "")


def validate_pdf(path: Path) -> tuple[bool, str]:
    if not path.exists():
        return False, "pdf_not_found"
    if path.stat().st_size <= 0:
        return False, "pdf_empty"
    with path.open("rb") as file:
        header = file.read(5)
    if header != b"%PDF-":
        return False, "pdf_invalid_header"
    return True, ""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="OpenSTD PDF discovery and download tool")
    subparsers = parser.add_subparsers(dest="command", required=True)

    discover_parser = subparsers.add_parser("discover")
    discover_parser.add_argument("--url", default=DEFAULT_SOURCE_URL)
    discover_parser.add_argument("--scope", default=ALL_NATIONAL_SCOPE)
    discover_parser.add_argument("--allowed-statuses", default=",".join(DEFAULT_ALLOWED_STATUSES))
    discover_parser.add_argument("--max-pages", type=int, default=0)
    discover_parser.add_argument("--max-items", type=int, default=0)
    discover_parser.add_argument("--interval", type=float, default=3.0)
    discover_parser.add_argument("--output-json", action="store_true")

    inspect_parser = subparsers.add_parser("inspect")
    inspect_parser.add_argument("--detail-url", required=True)
    inspect_parser.add_argument("--timeout", type=float, default=180.0)
    inspect_parser.add_argument("--output-json", action="store_true")

    download_parser = subparsers.add_parser("download")
    download_parser.add_argument("--detail-url", required=True)
    download_parser.add_argument("--output-dir", required=True)
    download_parser.add_argument("--timeout", type=float, default=180.0)
    download_parser.add_argument("--output-json", action="store_true")

    return parser


def dispatch(args: argparse.Namespace) -> dict[str, Any]:
    if args.command == "discover":
        allowed_statuses = {clean_text(item) for item in args.allowed_statuses.split(",") if clean_text(item)}
        return discover(
            args.url,
            scope=args.scope,
            max_pages=args.max_pages,
            max_items=args.max_items,
            interval_seconds=args.interval,
            allowed_statuses=allowed_statuses,
        )
    if args.command == "inspect":
        return inspect_detail(args.detail_url, timeout_seconds=args.timeout)
    if args.command == "download":
        return download(args.detail_url, output_dir=Path(args.output_dir), timeout_seconds=args.timeout)
    raise ValueError(f"Unsupported command: {args.command}")


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        payload = dispatch(args)
        json_dump(payload)
        return 0 if payload.get("status") not in {"failed"} else 2
    except Exception as exc:
        json_dump({"status": "failed", "reason": "tool_error", "message": str(exc)})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
