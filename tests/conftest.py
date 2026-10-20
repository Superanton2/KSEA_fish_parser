from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def fixtures_dir() -> Path:
    return FIXTURES


@pytest.fixture(scope="session")
def listing_html() -> str:
    return (FIXTURES / "listing_fish_and_seafood_p1.html").read_text(encoding="utf-8")


@pytest.fixture(scope="session")
def product_html() -> str:
    return (FIXTURES / "product_novus02877258000000.html").read_text(encoding="utf-8")
