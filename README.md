# Shopify Product Data Scraper v1.0.0

A small Python tool for exporting publicly available Shopify product data to CSV.

It reads Shopify store/category URLs from `stores.csv`, checks public Shopify JSON endpoints, paginates through products, expands product variants into CSV rows, and writes a clean export.

## What it collects

- store URL
- category URL
- source endpoint
- product ID
- handle
- title
- cleaned description
- vendor
- product type
- tags
- product URL
- variant ID
- variant title
- SKU
- price
- compare-at price
- availability, when exposed by Shopify
- image URLs
- created/updated timestamps, when exposed by Shopify

## Important limits

This tool only collects publicly available product data from public Shopify endpoints/pages.

It does not bypass logins, captchas, Cloudflare, anti-bot systems, or private APIs. It does not collect customer data, orders, accounts, or personal data.

If a store blocks or disables public JSON endpoints, the tool reports it in `output/errors.csv`.

## Project structure

`config.json`, `logs/`, `cache/`, and `output/` are local/generated runtime files and directories. They are intentionally ignored by Git. Create `config.json` locally from `config.example.json`; the runtime directories are created as needed when the scraper runs.

```text
shopify-product-scraper/
├── scraper.py
├── config.json              # local runtime config (not tracked)
├── config.example.json      # tracked configuration template
├── stores.csv
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── output/                  # generated output (not tracked)
│   ├── products.csv
│   ├── errors.csv
│   └── summary.json
├── logs/                    # generated logs (not tracked)
│   └── scraper.log
└── cache/                   # generated runtime state (not tracked)
    └── progress.json
```

## Input format

Edit `stores.csv`:

```csv
store_url,category_url
https://www.zoologistperfumes.com,https://www.zoologistperfumes.com/collections/samples-and-sets
https://www.zoologistperfumes.com,
https://json-ld-for-seo-demo.myshopify.com,
```

If `category_url` contains `/collections/{handle}`, the scraper uses:

```text
/collections/{handle}/products.json
```

If `category_url` is empty, the scraper uses:

```text
/products.json
```

## Python setup

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python scraper.py --input stores.csv --output output/products.csv
```

Simple run:

```powershell
python scraper.py
```

Append mode without duplicates:

```powershell
python scraper.py --input stores.csv --output output/products.csv --append
```

Skip endpoints already completed in `cache/progress.json`:

```powershell
python scraper.py --skip-completed
```

Reset progress:

```powershell
python scraper.py --reset-progress
```

Change request settings without editing code:

```powershell
python scraper.py --delay 1.0 --timeout 30 --max-pages 20
```

## Config

Copy `config.example.json` to `config.json` for local use. `config.json` contains local runtime settings and is intentionally ignored by Git:

```json
{
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
  "append": false,
  "skip_completed": false,
  "dedupe": true,
  "write_empty_targets_to_errors": true,
  "warn_on_empty_collection": true,
  "user_agent": "Mozilla/5.0 (compatible; PublicShopifyProductScraper/1.0.0; +https://example.com/bot)"
}
```

Command-line arguments override config values.

## Development and tests

Install development dependencies and run the offline test suite:

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

GitHub Actions runs the tests on Python 3.11 and 3.12 for pull requests and pushes to `main`.

## Docker run

Build and run with Docker Compose:

```bash
docker compose up --build
```

By default, Docker Compose uses the tracked `config.example.json`. To use a local `config.json`, set `CONFIG_FILE` before running Compose:

```bash
CONFIG_FILE=./config.json docker compose up --build
```

The container writes files to the local folders:

```text
output/products.csv
output/errors.csv
output/summary.json
logs/scraper.log
cache/progress.json
```

Run again with append mode:

```bash
docker compose run --rm shopify-scraper python scraper.py --append
```

Run with custom speed settings:

```bash
docker compose run --rm shopify-scraper python scraper.py --delay 1.0 --timeout 30
```

## Output files

### `output/products.csv`

Main product export. Each product variant becomes one CSV row.

### `output/errors.csv`

Problematic targets, including:

- `NO_PRODUCTS` — endpoint worked but returned zero products
- `ERROR` — request failed, endpoint blocked, non-JSON response, timeout, etc.

For empty category URLs, v1.0.0 writes a clearer message such as:

```text
Collection endpoint returned zero products. Check the collection handle/category URL, or use the full store export if the public collection JSON is empty.
```

### `output/summary.json`

Machine-readable run summary with version, config, counters, output paths, start and finish time.

## Notes for clients

Before running a real export, ask the client for:

1. Store/category URLs.
2. Exact CSV columns they need.
3. Whether it is a one-time export or reusable tool.
4. Confirmation that only publicly available product data should be collected.
5. A few example URLs for testing.

