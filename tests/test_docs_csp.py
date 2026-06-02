from pathlib import Path


DOCS_INDEX = Path(__file__).resolve().parents[1] / "docs" / "index.html"


def test_csp_allows_lambda_function_urls_in_any_region():
    html = DOCS_INDEX.read_text(encoding="utf-8")

    assert "connect-src https://*.on.aws" in html
    assert "connect-src https://*.lambda-url.us-east-1.on.aws" not in html


def test_endpoint_placeholder_is_region_neutral():
    html = DOCS_INDEX.read_text(encoding="utf-8")

    assert "https://abc.lambda-url.region.on.aws/" in html
    assert "placeholder=\"https://abc.lambda-url.us-east-1.on.aws/\"" not in html
