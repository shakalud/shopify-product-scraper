import argparse
import csv
import json
import logging
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from html import unescape
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup


PROJECT_DIR = Path(__file__).resolve().parent

DEFAULT_CONFIG = {
    "input_csv": "stores.csv",
    "output_csv": "output/products.csv",
    "errors_csv": "output/errors.csv",
    "summary_json": "output/summary.json",
    "log_file": "logs/scraper.log",
    "progress_file": "cache/progress.json",
    "limit": 250,
    "max_pages": 100,
    "timeout_seconds": 20,
    "delay_seconds": 0.5,
    "retries": 3,
    "append": False,
    "skip_completed": False,
    "dedupe": True,
    "write_empty_targets_to_errors": True,
    "warn_on_empty_collection": True,
    "user_agent": "Mozilla/5.0 (compatible; PublicShopifyProductScraper/1.0.0; +https://example.com/bot)",
}

CSV_FIELDS = [
    "store_url",
    "category_url",
    "source_endpoint",
    "product_id",
    "handle",
    "title",
    "description",
    "vendor",
    "product_type",
    "tags",
    "product_url",
    "variant_id",
    "variant_title",
    "sku",
    "price",
    "compare_at_price",
    "available",
    "image_urls",
    "created_at",
    "updated_at",
]

ERROR_FIELDS = [
    "timestamp",
    "store_url",
    "category_url",
    "source_endpoint",
    "status",
    "message",
]


@dataclass
class Target:
    store_url: str
    category_url: str = ""


@dataclass
class ScrapeResult:
    endpoint: str
    rows: List[Dict[str, Any]]
    product_count: int
    page_count: int


def project_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else PROJECT_DIR / path


def load_config(config_path: Optional[Path] = None) -> Dict[str, Any]:
    config = DEFAULT_CONFIG.copy()
    chosen_path = config_path or (PROJECT_DIR / "config.json")
    example_path = PROJECT_DIR / "config.example.json"

    if chosen_path.exists():
        config.update(json.loads(chosen_path.read_text(encoding="utf-8")))
    elif not config_path and example_path.exists():
        config.update(json.loads(example_path.read_text(encoding="utf-8")))
    return config


def setup_logging(log_file: Path) -> None:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
        force=True,
    )


def normalize_base_url(url: str) -> str:
    url = (url or "").strip()
    if not url:
        raise ValueError("Empty store_url")
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    parsed = urlparse(url)
    if not parsed.netloc:
        raise ValueError(f"Invalid store_url: {url}")
    return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")


def clean_text(html: Any) -> str:
    if not html:
        return ""
    text = BeautifulSoup(str(html), "html.parser").get_text(" ", strip=True)
    text = unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def is_collection_url(category_url: str) -> bool:
    if not category_url:
        return False
    parsed = urlparse(category_url.strip())
    parts = [p for p in parsed.path.split("/") if p]
    return "collections" in parts


def collection_handle_from_url(category_url: str) -> Optional[str]:
    if not category_url:
        return None
    parsed = urlparse(category_url.strip())
    parts = [p for p in parsed.path.split("/") if p]
    if "collections" not in parts:
        return None
    index = parts.index("collections")
    if len(parts) <= index + 1:
        return None
    return parts[index + 1]


def endpoint_for_target(target: Target) -> str:
    base = normalize_base_url(target.store_url)
    handle = collection_handle_from_url(target.category_url)
    if handle:
        return f"{base}/collections/{handle}/products.json"
    return f"{base}/products.json"


def load_targets(input_csv: Path) -> List[Target]:
    if not input_csv.exists():
        raise FileNotFoundError(f"Input CSV not found: {input_csv}")

    targets: List[Target] = []
    with input_csv.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames or "store_url" not in reader.fieldnames:
            raise ValueError("Input CSV must contain at least a store_url column")

        for line_number, row in enumerate(reader, start=2):
            store_url = (row.get("store_url") or "").strip()
            category_url = (row.get("category_url") or "").strip()
            if not store_url:
                logging.warning("Skipping row %s: empty store_url", line_number)
                continue
            targets.append(Target(store_url=store_url, category_url=category_url))
    return targets


def load_progress(progress_file: Path) -> Dict[str, Any]:
    if progress_file.exists():
        try:
            return json.loads(progress_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            logging.warning("Progress file is corrupted, starting with empty progress.")
    return {"completed_endpoints": []}


def save_progress(progress_file: Path, progress: Dict[str, Any]) -> None:
    progress_file.parent.mkdir(parents=True, exist_ok=True)
    progress_file.write_text(json.dumps(progress, indent=2, ensure_ascii=False), encoding="utf-8")


def reset_outputs(output_csv: Path, errors_csv: Path, summary_json: Path, progress_file: Path, reset_progress: bool) -> None:
    for path in [output_csv, errors_csv, summary_json]:
        if path.exists():
            path.unlink()
    if reset_progress and progress_file.exists():
        progress_file.unlink()


def request_json(
    session: requests.Session,
    url: str,
    params: Dict[str, Any],
    timeout: int,
    retries: int,
    delay: float,
) -> Dict[str, Any]:
    last_error: Optional[Exception] = None
    for attempt in range(1, retries + 1):
        try:
            response = session.get(url, params=params, timeout=timeout)
            response.raise_for_status()
            content_type = response.headers.get("content-type", "")
            if "json" not in content_type.lower() and not response.text.lstrip().startswith("{"):
                raise ValueError(f"Non-JSON response: {content_type}")
            return response.json()
        except Exception as exc:
            last_error = exc
            logging.warning("Request failed (%s/%s): %s | %s", attempt, retries, response_url(url, params), exc)
            if attempt < retries:
                time.sleep(delay * attempt)
    raise RuntimeError(f"Failed after {retries} retries: {response_url(url, params)} | {last_error}")


def response_url(url: str, params: Dict[str, Any]) -> str:
    if not params:
        return url
    query = "&".join(f"{k}={v}" for k, v in params.items())
    return f"{url}?{query}"


def row_key(row: Dict[str, Any]) -> Tuple[str, str, str, str]:
    return (
        str(row.get("store_url", "")),
        str(row.get("product_id", "")),
        str(row.get("variant_id", "")),
        str(row.get("source_endpoint", "")),
    )


def load_existing_keys(output_csv: Path) -> Set[Tuple[str, str, str, str]]:
    keys: Set[Tuple[str, str, str, str]] = set()
    if not output_csv.exists() or output_csv.stat().st_size == 0:
        return keys
    with output_csv.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            keys.add(row_key(row))
    return keys


def product_to_rows(product: Dict[str, Any], target: Target, endpoint: str) -> Iterable[Dict[str, Any]]:
    base = normalize_base_url(target.store_url)
    handle = product.get("handle") or ""
    product_url = urljoin(base, f"/products/{handle}") if handle else base
    images = product.get("images") or []
    image_urls = [img.get("src") for img in images if isinstance(img, dict) and img.get("src")]
    tags = product.get("tags") or []
    tags_text = ", ".join(str(tag) for tag in tags) if isinstance(tags, list) else str(tags)

    variants = product.get("variants") or [{}]
    for variant in variants:
        if not isinstance(variant, dict):
            variant = {}
        yield {
            "store_url": base,
            "category_url": target.category_url,
            "source_endpoint": endpoint,
            "product_id": product.get("id", ""),
            "handle": handle,
            "title": product.get("title", ""),
            "description": clean_text(product.get("body_html", "")),
            "vendor": product.get("vendor", ""),
            "product_type": product.get("product_type", ""),
            "tags": tags_text,
            "product_url": product_url,
            "variant_id": variant.get("id", ""),
            "variant_title": variant.get("title", ""),
            "sku": variant.get("sku", ""),
            "price": variant.get("price", ""),
            "compare_at_price": variant.get("compare_at_price", ""),
            "available": variant.get("available", ""),
            "image_urls": " | ".join(image_urls),
            "created_at": product.get("created_at", ""),
            "updated_at": product.get("updated_at", ""),
        }


def scrape_target(session: requests.Session, target: Target, config: Dict[str, Any]) -> ScrapeResult:
    endpoint = endpoint_for_target(target)
    logging.info("Scraping endpoint: %s", endpoint)
    rows: List[Dict[str, Any]] = []
    seen_product_ids = set()
    product_count = 0
    page_count = 0

    for page in range(1, int(config["max_pages"]) + 1):
        data = request_json(
            session=session,
            url=endpoint,
            params={"limit": int(config["limit"]), "page": page},
            timeout=int(config["timeout_seconds"]),
            retries=int(config["retries"]),
            delay=float(config["delay_seconds"]),
        )
        products = data.get("products") or []
        if not products:
            logging.info("No more products at page %s for %s", page, endpoint)
            break

        page_count += 1
        new_count = 0
        for product in products:
            product_id = product.get("id")
            # Avoid duplicates if a store ignores page=N and repeats page 1.
            if product_id and product_id in seen_product_ids:
                continue
            if product_id:
                seen_product_ids.add(product_id)
            new_count += 1
            product_count += 1
            rows.extend(product_to_rows(product, target, endpoint))

        logging.info("Page %s: %s new products, %s CSV rows so far", page, new_count, len(rows))
        if new_count == 0 or len(products) < int(config["limit"]):
            break
        time.sleep(float(config["delay_seconds"]))

    return ScrapeResult(endpoint=endpoint, rows=rows, product_count=product_count, page_count=page_count)


def write_rows(output_csv: Path, rows: List[Dict[str, Any]], append: bool = True) -> None:
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    file_exists = output_csv.exists() and output_csv.stat().st_size > 0
    mode = "a" if append else "w"
    with output_csv.open(mode, encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        if not file_exists or not append:
            writer.writeheader()
        writer.writerows(rows)


def write_error(errors_csv: Path, target: Target, endpoint: str, status: str, message: str) -> None:
    errors_csv.parent.mkdir(parents=True, exist_ok=True)
    file_exists = errors_csv.exists() and errors_csv.stat().st_size > 0
    with errors_csv.open("a", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=ERROR_FIELDS)
        if not file_exists:
            writer.writeheader()
        writer.writerow(
            {
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "store_url": target.store_url,
                "category_url": target.category_url,
                "source_endpoint": endpoint,
                "status": status,
                "message": message,
            }
        )


def write_summary(summary_json: Path, summary: Dict[str, Any]) -> None:
    summary_json.parent.mkdir(parents=True, exist_ok=True)
    summary_json.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")


def dedupe_rows(rows: List[Dict[str, Any]], existing_keys: Set[Tuple[str, str, str, str]]) -> Tuple[List[Dict[str, Any]], int]:
    unique_rows: List[Dict[str, Any]] = []
    skipped = 0
    for row in rows:
        key = row_key(row)
        if key in existing_keys:
            skipped += 1
            continue
        existing_keys.add(key)
        unique_rows.append(row)
    return unique_rows, skipped


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export publicly available Shopify product data to CSV.")
    parser.add_argument("--config", default=None, help="Path to config JSON. Default: config.json or config.example.json")
    parser.add_argument("--input", default=None, help="Input stores CSV. Overrides config input_csv.")
    parser.add_argument("--output", default=None, help="Output products CSV. Overrides config output_csv.")
    parser.add_argument("--errors", default=None, help="Output errors CSV. Overrides config errors_csv.")
    parser.add_argument("--summary", default=None, help="Output summary JSON. Overrides config summary_json.")
    parser.add_argument("--append", action="store_true", help="Append to existing output CSV instead of replacing it.")
    parser.add_argument("--skip-completed", action="store_true", help="Skip endpoints already listed in cache/progress.json.")
    parser.add_argument("--reset-progress", action="store_true", help="Delete cache/progress.json before running.")
    parser.add_argument("--no-dedupe", action="store_true", help="Do not deduplicate rows within this run/output file.")
    parser.add_argument("--limit", type=int, default=None, help="Shopify page size. Default from config: 250")
    parser.add_argument("--max-pages", type=int, default=None, help="Maximum pages per endpoint.")
    parser.add_argument("--delay", type=float, default=None, help="Delay between requests in seconds. Overrides config delay_seconds.")
    parser.add_argument("--timeout", type=int, default=None, help="Request timeout in seconds. Overrides config timeout_seconds.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = load_config(project_path(args.config) if args.config else None)

    if args.input:
        config["input_csv"] = args.input
    if args.output:
        config["output_csv"] = args.output
    if args.errors:
        config["errors_csv"] = args.errors
    if args.summary:
        config["summary_json"] = args.summary
    if args.limit is not None:
        config["limit"] = args.limit
    if args.max_pages is not None:
        config["max_pages"] = args.max_pages
    if args.delay is not None:
        config["delay_seconds"] = args.delay
    if args.timeout is not None:
        config["timeout_seconds"] = args.timeout
    if args.append:
        config["append"] = True
    if args.skip_completed:
        config["skip_completed"] = True
    if args.no_dedupe:
        config["dedupe"] = False

    log_file = project_path(config["log_file"])
    setup_logging(log_file)

    input_csv = project_path(config["input_csv"])
    output_csv = project_path(config["output_csv"])
    errors_csv = project_path(config["errors_csv"])
    summary_json = project_path(config["summary_json"])
    progress_file = project_path(config["progress_file"])

    if not bool(config.get("append", False)):
        reset_outputs(output_csv, errors_csv, summary_json, progress_file, reset_progress=args.reset_progress)
    elif args.reset_progress and progress_file.exists():
        progress_file.unlink()

    targets = load_targets(input_csv)
    progress = load_progress(progress_file)
    completed = set(progress.get("completed_endpoints", []))

    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": config["user_agent"],
            "Accept": "application/json,text/html;q=0.9,*/*;q=0.8",
        }
    )

    existing_keys = load_existing_keys(output_csv) if bool(config.get("dedupe", True)) else set()

    summary: Dict[str, Any] = {
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "version": "1.0.0",
        "config": {
            "limit": config.get("limit"),
            "max_pages": config.get("max_pages"),
            "timeout_seconds": config.get("timeout_seconds"),
            "delay_seconds": config.get("delay_seconds"),
            "retries": config.get("retries"),
            "append": config.get("append"),
            "skip_completed": config.get("skip_completed"),
            "dedupe": config.get("dedupe"),
        },
        "input_csv": str(input_csv),
        "output_csv": str(output_csv),
        "errors_csv": str(errors_csv),
        "targets_loaded": len(targets),
        "endpoints_processed": 0,
        "endpoints_skipped": 0,
        "endpoints_with_no_products": 0,
        "products_found": 0,
        "rows_exported": 0,
        "duplicate_rows_skipped": 0,
        "errors": 0,
    }

    logging.info("Loaded %s targets", len(targets))
    for target in targets:
        endpoint = ""
        try:
            endpoint = endpoint_for_target(target)
            if bool(config.get("skip_completed", False)) and endpoint in completed:
                logging.info("Skipping completed endpoint: %s", endpoint)
                summary["endpoints_skipped"] += 1
                continue

            result = scrape_target(session, target, config)
            rows_to_write = result.rows
            skipped_duplicates = 0
            if bool(config.get("dedupe", True)):
                rows_to_write, skipped_duplicates = dedupe_rows(rows_to_write, existing_keys)

            write_rows(output_csv, rows_to_write, append=True)

            summary["endpoints_processed"] += 1
            summary["products_found"] += result.product_count
            summary["rows_exported"] += len(rows_to_write)
            summary["duplicate_rows_skipped"] += skipped_duplicates

            if result.product_count == 0 and bool(config.get("write_empty_targets_to_errors", True)):
                summary["endpoints_with_no_products"] += 1
                
                empty_message = "Endpoint returned zero products."
                if is_collection_url(target.category_url):
                    empty_message = (
                        "Collection endpoint returned zero products. "
                        "Check the collection handle/category URL, or use the full store export if the public collection JSON is empty."
                    )
                    if bool(config.get("warn_on_empty_collection", True)):
                        logging.warning("%s | %s", empty_message, result.endpoint)
                else:
                    empty_message = "Store endpoint returned zero products. Check whether the store exposes public Shopify products JSON."
                write_error(errors_csv, target, result.endpoint, "NO_PRODUCTS", empty_message)

            completed.add(result.endpoint)
            progress["completed_endpoints"] = sorted(completed)
            save_progress(progress_file, progress)

            logging.info(
                "Saved %s rows from %s | products=%s | duplicates_skipped=%s",
                len(rows_to_write),
                result.endpoint,
                result.product_count,
                skipped_duplicates,
            )
        except Exception as exc:
            summary["errors"] += 1
            if not endpoint:
                try:
                    endpoint = endpoint_for_target(target)
                except Exception:
                    endpoint = ""
            write_error(errors_csv, target, endpoint, "ERROR", str(exc))
            logging.exception("Target failed: store=%s category=%s error=%s", target.store_url, target.category_url, exc)

    summary["finished_at"] = datetime.now().isoformat(timespec="seconds")
    write_summary(summary_json, summary)

    logging.info(
        "Done. Endpoints processed: %s | Products: %s | Rows exported: %s | No-products endpoints: %s | Errors: %s | Output: %s",
        summary["endpoints_processed"],
        summary["products_found"],
        summary["rows_exported"],
        summary["endpoints_with_no_products"],
        summary["errors"],
        output_csv,
    )
    print("\nSummary")
    print("-------")
    print(f"Targets loaded: {summary['targets_loaded']}")
    print(f"Endpoints processed: {summary['endpoints_processed']}")
    print(f"Products found: {summary['products_found']}")
    print(f"Rows exported: {summary['rows_exported']}")
    print(f"Duplicate rows skipped: {summary['duplicate_rows_skipped']}")
    print(f"No-products endpoints: {summary['endpoints_with_no_products']}")
    print(f"Errors: {summary['errors']}")
    print(f"Products CSV: {output_csv}")
    print(f"Errors CSV: {errors_csv}")
    print(f"Summary JSON: {summary_json}")


if __name__ == "__main__":
    main()
