"""市场源地址解析：四种合法形态落到索引地址与规范键，其余形态拒绝。"""

from __future__ import annotations

import pytest

from lib.market.address import SourceAddressError, SourceAddressErrorCode, resolve_source_address

RAW = "https://raw.githubusercontent.com"


@pytest.mark.parametrize(
    ("address", "index_url", "canonical_key"),
    [
        (
            "ArcReel/arcreel-market",
            f"{RAW}/ArcReel/arcreel-market/HEAD/arcreel-market.json",
            "github:ArcReel/arcreel-market@HEAD",
        ),
        (
            "  someone/my.market_repo  ",
            f"{RAW}/someone/my.market_repo/HEAD/arcreel-market.json",
            "github:someone/my.market_repo@HEAD",
        ),
        (
            "someone/market@v1.2",
            f"{RAW}/someone/market/v1.2/arcreel-market.json",
            "github:someone/market@v1.2",
        ),
        (
            "someone/market@feature/new-entries",
            f"{RAW}/someone/market/feature/new-entries/arcreel-market.json",
            "github:someone/market@feature/new-entries",
        ),
        (
            "https://github.com/someone/market",
            f"{RAW}/someone/market/HEAD/arcreel-market.json",
            "github:someone/market@HEAD",
        ),
        (
            "https://github.com/someone/market.git/",
            f"{RAW}/someone/market/HEAD/arcreel-market.json",
            "github:someone/market@HEAD",
        ),
        (
            "https://github.com/someone/market/tree/dev",
            f"{RAW}/someone/market/dev/arcreel-market.json",
            "github:someone/market@dev",
        ),
        (
            "https://github.com/someone/market/tree/release/2026",
            f"{RAW}/someone/market/release/2026/arcreel-market.json",
            "github:someone/market@release/2026",
        ),
        (
            "https://mirror.example.com/markets/team/arcreel-market.json",
            "https://mirror.example.com/markets/team/arcreel-market.json",
            "url:https://mirror.example.com/markets/team/arcreel-market.json",
        ),
        (
            f"{RAW}/someone/market/main/arcreel-market.json",
            f"{RAW}/someone/market/main/arcreel-market.json",
            f"url:{RAW}/someone/market/main/arcreel-market.json",
        ),
    ],
)
def test_accepted_address_forms_resolve_to_index_url_and_canonical_key(
    address: str, index_url: str, canonical_key: str
) -> None:
    resolved = resolve_source_address(address)

    assert resolved.index_url == index_url
    assert resolved.canonical_key == canonical_key


def test_same_repository_written_differently_shares_one_canonical_key() -> None:
    keys = {
        resolve_source_address(address).canonical_key
        for address in (
            "someone/market",
            "someone/market@HEAD",
            "https://github.com/someone/market",
            "https://github.com/someone/market/tree/HEAD",
        )
    }

    assert keys == {"github:someone/market@HEAD"}


@pytest.mark.parametrize(
    ("address", "code"),
    [
        ("", SourceAddressErrorCode.EMPTY),
        ("   ", SourceAddressErrorCode.EMPTY),
        ("http://github.com/someone/market", SourceAddressErrorCode.INSECURE_SCHEME),
        ("http://mirror.example.com/arcreel-market.json", SourceAddressErrorCode.INSECURE_SCHEME),
        ("git@github.com:someone/market.git", SourceAddressErrorCode.UNSUPPORTED),
        ("/srv/markets/arcreel-market.json", SourceAddressErrorCode.UNSUPPORTED),
        ("./markets/team", SourceAddressErrorCode.UNSUPPORTED),
        ("~/markets/team", SourceAddressErrorCode.UNSUPPORTED),
        ("C:\\markets\\arcreel-market.json", SourceAddressErrorCode.UNSUPPORTED),
        ("file:///srv/markets/arcreel-market.json", SourceAddressErrorCode.UNSUPPORTED),
        ("someone", SourceAddressErrorCode.UNSUPPORTED),
        ("someone/market/extra", SourceAddressErrorCode.UNSUPPORTED),
        ("someone/market@", SourceAddressErrorCode.UNSUPPORTED),
        ("someone/market@../../etc", SourceAddressErrorCode.UNSUPPORTED),
        ("someone/..", SourceAddressErrorCode.UNSUPPORTED),
        ("https://mirror.example.com/markets/index.json", SourceAddressErrorCode.UNSUPPORTED),
        ("https://mirror.example.com/arcreel-market.json?token=1", SourceAddressErrorCode.UNSUPPORTED),
        ("https://github.com/someone/market/blob/main/arcreel-market.json", SourceAddressErrorCode.UNSUPPORTED),
        ("https://github.com/someone", SourceAddressErrorCode.UNSUPPORTED),
        ("https://user:pass@mirror.example.com/arcreel-market.json", SourceAddressErrorCode.UNSUPPORTED),
    ],
)
def test_rejected_address_forms(address: str, code: SourceAddressErrorCode) -> None:
    with pytest.raises(SourceAddressError) as excinfo:
        resolve_source_address(address)

    assert excinfo.value.code is code
