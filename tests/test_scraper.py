import json

import pytest

from scraper import (
    Target,
    clean_text,
    collection_handle_from_url,
    dedupe_rows,
    endpoint_for_target,
    load_config,
    load_targets,
    normalize_base_url,
    product_to_rows,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("example.com", "https://example.com"),
        (" https://example.com/path ", "https://example.com"),
        ("http://shop.example.com/", "http://shop.example.com"),
    ],
)
def test_normalize_base_url(value, expected):
    assert normalize_base_url(value) == expected


def test_normalize_base_url_rejects_empty_value():
    with pytest.raises(ValueError, match="Empty store_url"):
        normalize_base_url("")


def test_collection_endpoint_is_built_from_category_url():
    category = "https://example.com/collections/samples/products"
    target = Target("example.com", category)

    assert collection_handle_from_url(category) == "samples"
    assert endpoint_for_target(target) == (
        "https://example.com/collections/samples/products.json"
    )


def test_store_endpoint_is_used_without_collection():
    assert endpoint_for_target(Target("example.com")) == (
        "https://example.com/products.json"
    )


def test_clean_text_removes_markup_and_normalizes_whitespace():
    assert clean_text("<p>Hello&nbsp; <strong>world</strong></p>") == "Hello world"


def test_load_config_merges_explicit_file_with_defaults(tmp_path):
    config_file = tmp_path / "config.json"
    config_file.write_text(
        json.dumps({"delay_seconds": 1.25}),
        encoding="utf-8",
    )

    config = load_config(config_file)

    assert config["delay_seconds"] == 1.25
    assert config["limit"] == 250


def test_load_targets_reads_rows_and_skips_empty_store(tmp_path):
    input_file = tmp_path / "stores.csv"
    input_file.write_text(
        "store_url,category_url\n"
        "example.com,https://example.com/collections/test\n"
        ",https://ignored.example.com/collections/test\n",
        encoding="utf-8",
    )

    assert load_targets(input_file) == [
        Target("example.com", "https://example.com/collections/test")
    ]


def test_product_to_rows_expands_variants():
    product = {
        "id": 1,
        "handle": "sample",
        "title": "Sample",
        "body_html": "<p>Clean <b>description</b></p>",
        "tags": ["one", "two"],
        "images": [{"src": "https://cdn.example.com/image.jpg"}],
        "variants": [
            {"id": 10, "sku": "A", "price": "10.00"},
            {"id": 11, "sku": "B", "price": "12.00"},
        ],
    }

    rows = list(
        product_to_rows(
            product,
            Target("example.com"),
            "https://example.com/products.json",
        )
    )

    assert [row["variant_id"] for row in rows] == [10, 11]
    assert rows[0]["description"] == "Clean description"
    assert rows[0]["tags"] == "one, two"
    assert rows[0]["product_url"] == "https://example.com/products/sample"


def test_dedupe_rows_removes_existing_and_repeated_rows():
    row = {
        "store_url": "https://example.com",
        "product_id": 1,
        "variant_id": 10,
        "source_endpoint": "https://example.com/products.json",
    }

    unique, skipped = dedupe_rows([row, row.copy()], set())

    assert unique == [row]
    assert skipped == 1
