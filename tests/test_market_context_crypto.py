# -*- coding: utf-8 -*-
"""Offline tests for crypto market context (prompt role/guidelines + profile).

Stocks must keep their existing market context; crypto must explicitly avoid
A-share concepts.
"""

from __future__ import annotations

import pytest

from src.core.market_profile import CN_PROFILE, CRYPTO_PROFILE, get_profile
from src.market_context import (
    detect_market,
    get_market_guidelines,
    get_market_role,
)


@pytest.mark.parametrize(
    "code",
    ["BTC-USDT", "btc-usdt", "BTCUSDT", "btcusdt", "ETH-BTC", "SOLUSDC"],
)
def test_detect_market_returns_crypto_first(code: str) -> None:
    assert detect_market(code) == "crypto"


@pytest.mark.parametrize(
    "code,expected",
    [
        ("600519", "cn"),
        ("000300", "cn"),
        ("AAPL", "us"),
        ("BRK.B", "us"),
        ("HK00700", "hk"),
        ("00700.HK", "hk"),
        ("7203.T", "jp"),
        ("005930.KS", "kr"),
        ("2330.TW", "tw"),
        # Ambiguity rule: a bare base asset is still a US ticker.
        ("ADA", "us"),
        ("BTC", "us"),
        ("", "cn"),
        (None, "cn"),
    ],
)
def test_detect_market_keeps_stock_semantics(code, expected) -> None:
    assert detect_market(code) == expected


def test_crypto_role_is_rendered_for_both_languages() -> None:
    assert "虚拟货币" in get_market_role("BTC-USDT", "zh")
    assert "crypto" in get_market_role("BTC-USDT", "en").lower()
    # Korean output reuses the English scaffolding
    assert "crypto" in get_market_role("BTC-USDT", "ko").lower()
    # Stocks are untouched
    assert get_market_role("600519", "zh") == " A 股"
    assert get_market_role("AAPL", "en") == "US stock"


def test_crypto_guidelines_are_24_7_and_forbid_a_share_concepts_zh() -> None:
    guidelines = get_market_guidelines("BTC-USDT", "zh")

    for phrase in ("7×24", "涨跌停", "T+1", "现货", "永续", "资金费率", "链上"):
        assert phrase in guidelines, f"missing {phrase!r} in zh crypto guidelines"

    # explicit "do not apply" wording with the A-share-only concepts
    for forbidden in ("北向资金", "龙虎榜", "融资融券", "板块轮动"):
        assert forbidden in guidelines, f"missing {forbidden!r} in zh crypto guidelines"
    assert "不要" in guidelines


def test_crypto_guidelines_are_24_7_and_forbid_a_share_concepts_en() -> None:
    guidelines = get_market_guidelines("BTC-USDT", "en")

    for phrase in ("24/7", "no daily price limit", "spot", "perpetuals", "funding rate", "on-chain"):
        assert phrase in guidelines, f"missing {phrase!r} in en crypto guidelines"

    for forbidden in ("price-limit boards", "Northbound", "Dragon Tiger", "margin-financing"):
        assert forbidden in guidelines, f"missing {forbidden!r} in en crypto guidelines"
    assert "Do not apply" in guidelines


def test_stock_guidelines_unchanged_by_crypto_addition() -> None:
    cn = get_market_guidelines("600519", "zh")
    assert "A 股" in cn
    assert "T+1" in cn
    us = get_market_guidelines("AAPL", "en")
    assert "US stock" in us


def test_market_profile_lookup_resolves_crypto() -> None:
    assert get_profile("crypto") is CRYPTO_PROFILE
    assert get_profile("BTC-USDT") is CRYPTO_PROFILE
    assert get_profile("btcusdt") is CRYPTO_PROFILE
    assert CRYPTO_PROFILE.region == "crypto"
    assert CRYPTO_PROFILE.has_market_stats is False
    assert CRYPTO_PROFILE.has_sector_rankings is False
    assert "涨跌停" in CRYPTO_PROFILE.prompt_index_hint


def test_market_profile_lookup_keeps_existing_regions() -> None:
    assert get_profile("cn") is CN_PROFILE
    assert get_profile("us").region == "us"
    assert get_profile("hk").region == "hk"
    assert get_profile("jp").region == "jp"
    assert get_profile("kr").region == "kr"
    assert get_profile("tw").region == "tw"
    # unknown / invalid regions keep the historical CN fallback
    assert get_profile("eu").region == "cn"
    assert get_profile("").region == "cn"
