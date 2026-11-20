# -*- coding: utf-8 -*-
"""Offline tests for the virtual-currency symbol layer.

Covers:
* ``src.services.market_symbol_utils`` crypto helpers + the ambiguity rule;
* ``parse_analysis_target`` crypto targets (existing stock contracts untouched);
* data-provider market routing / ordering for ``crypto``;
* 24/7 trading-calendar semantics.

No network access: every provider call is either not reached or monkeypatched.
"""

from __future__ import annotations

from datetime import date

import pytest

from src.services.market_symbol_utils import (
    CRYPTO_BASE_ASSETS,
    CRYPTO_QUOTE_ASSETS,
    crypto_to_binance,
    crypto_to_yahoo,
    is_crypto_symbol,
    normalize_crypto_symbol,
    split_crypto_symbol,
)
from src.services.stock_list_parser import ParseStatus, parse_analysis_target


class _FakeFetcher:
    """Minimal BaseFetcher stand-in for pure routing assertions."""

    def __init__(self, name: str, priority: int = 99) -> None:
        self.name = name
        self.priority = priority


# ---------------------------------------------------------------------------
# Symbol helpers
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "raw",
    ["BTC-USDT", "btc-usdt", "BTCUSDT", "btcusdt", " btcusdt ", "Btc-Usdt"],
)
def test_normalize_crypto_symbol_canonicalizes_pair(raw: str) -> None:
    assert normalize_crypto_symbol(raw) == "BTC-USDT"
    assert is_crypto_symbol(raw) is True
    assert split_crypto_symbol(raw) == ("BTC", "USDT")


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("eth-usdc", "ETH-USDC"),
        ("SOLUSDC", "SOL-USDC"),
        ("ETHBTC", "ETH-BTC"),
        ("XRPUSD", "XRP-USD"),
        ("dogeusdt", "DOGE-USDT"),
        ("SHIBUSDT", "SHIB-USDT"),
        ("RENDERUSDT", "RENDER-USDT"),
    ],
)
def test_normalize_crypto_symbol_accepts_allowlisted_pairs(raw: str, expected: str) -> None:
    assert normalize_crypto_symbol(raw) == expected


@pytest.mark.parametrize(
    "raw",
    ["", None, "AAPL", "MSFT", "SHOP", "600519", "00700.HK", "7203.T", "2330.TW"],
)
def test_non_crypto_symbols_are_rejected(raw) -> None:
    assert normalize_crypto_symbol(raw) is None
    assert is_crypto_symbol(raw) is False


def test_ambiguity_rule_bare_base_asset_stays_stock() -> None:
    """A bare base asset without a quote keeps the existing US-ticker path."""
    assert "ADA" in CRYPTO_BASE_ASSETS
    assert "BTC" in CRYPTO_BASE_ASSETS
    assert is_crypto_symbol("ADA") is False
    assert is_crypto_symbol("BTC") is False
    # 2-letter bases exist (AR / OP) but a bare one is still not a pair.
    assert is_crypto_symbol("AR") is False
    assert is_crypto_symbol("OP") is False


def test_unknown_quote_is_not_crypto() -> None:
    assert is_crypto_symbol("BTC-XRP") is False
    assert is_crypto_symbol("BTCZZZ") is False
    assert is_crypto_symbol("NOTACOINUSDT") is False
    assert "XRP" in CRYPTO_BASE_ASSETS
    assert "USDT" in CRYPTO_QUOTE_ASSETS


def test_exchange_symbol_mapping() -> None:
    assert crypto_to_binance("BTC-USDT") == "BTCUSDT"
    assert crypto_to_binance("btc-usdt") == "BTCUSDT"
    assert crypto_to_binance("AAPL") is None
    assert crypto_to_yahoo("BTC-USDT") == "BTC-USD"
    assert crypto_to_yahoo("ETH-BTC") == "ETH-BTC"
    assert crypto_to_yahoo("AAPL") is None


# ---------------------------------------------------------------------------
# Analysis-target parsing
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("raw", ["BTC-USDT", "btc-usdt", "BTCUSDT", "btcusdt"])
def test_parse_analysis_target_marks_crypto(raw: str) -> None:
    target = parse_analysis_target(raw)
    assert target.asset_type == ParseStatus.STOCK
    assert target.exchange == "CRYPTO"
    assert target.canonical_id == "BTC-USDT"
    assert target.display_code == "BTC-USDT"
    assert target.normalized_code == "BTC-USDT"
    assert target.normalized_prefix is None


def test_parse_analysis_target_keeps_stock_contracts() -> None:
    assert parse_analysis_target("AAPL").exchange == "US"
    assert parse_analysis_target("AAPL").canonical_id == "AAPL"
    assert parse_analysis_target("600519").exchange == "SH"
    assert parse_analysis_target("00700.HK").exchange == "HK"
    assert parse_analysis_target("7203.T").exchange == "T"
    assert parse_analysis_target("2330.TW").exchange == "TW"


def test_detect_market_reports_crypto_first() -> None:
    from src.market_context import detect_market

    assert detect_market("BTC-USDT") == "crypto"
    assert detect_market("BTCUSDT") == "crypto"
    # stocks keep their previous detection
    assert detect_market("AAPL") == "us"
    assert detect_market("600519") == "cn"
    assert detect_market("HK00700") == "hk"
    assert detect_market("7203.T") == "jp"
    assert detect_market("2330.TW") == "tw"


# ---------------------------------------------------------------------------
# Data-provider routing
# ---------------------------------------------------------------------------
def test_daily_routing_declares_crypto_only_sources() -> None:
    from data_provider.base import DataFetcherManager

    support = DataFetcherManager._DAILY_MARKET_FETCHER_SUPPORT
    assert support["BinanceVisionFetcher"] == {"crypto"}
    assert support["YahooCryptoFetcher"] == {"crypto"}

    fetchers = [_FakeFetcher(name) for name in (
        "EfinanceFetcher",
        "AkshareFetcher",
        "YfinanceFetcher",
        "BinanceVisionFetcher",
        "YahooCryptoFetcher",
    )]
    kept = DataFetcherManager._filter_daily_fetchers_for_market(fetchers, "crypto")
    assert [fetcher.name for fetcher in kept] == ["BinanceVisionFetcher", "YahooCryptoFetcher"]


def test_crypto_source_order_is_fixed() -> None:
    from data_provider.base import DataFetcherManager

    fetchers = [
        _FakeFetcher("YahooCryptoFetcher", priority=3),
        _FakeFetcher("BinanceVisionFetcher", priority=1),
        _FakeFetcher("YfinanceFetcher", priority=4),
    ]
    ordered = DataFetcherManager._crypto_fetchers(fetchers)
    assert [fetcher.name for fetcher in ordered] == ["BinanceVisionFetcher", "YahooCryptoFetcher"]


def test_a_share_chain_excludes_crypto_sources() -> None:
    from data_provider.base import DataFetcherManager

    fetchers = [
        _FakeFetcher("EfinanceFetcher"),
        _FakeFetcher("BinanceVisionFetcher"),
        _FakeFetcher("YahooCryptoFetcher"),
    ]
    kept = DataFetcherManager._exclude_crypto_fetchers(fetchers)
    assert [fetcher.name for fetcher in kept] == ["EfinanceFetcher"]


def test_crypto_daily_routing_never_uses_stock_fetchers() -> None:
    import pandas as pd

    from data_provider.base import DataFetcherManager

    calls = []

    class _DailyFetcher:
        def __init__(self, name: str) -> None:
            self.name = name
            self.priority = 1

        def get_daily_data(self, stock_code, start_date=None, end_date=None, days=30):
            calls.append((self.name, stock_code))
            return pd.DataFrame(
                {"date": [pd.Timestamp("2024-01-01")], "close": [1.0], "volume": [1.0]}
            )

    manager = DataFetcherManager(
        fetchers=[_DailyFetcher("EfinanceFetcher"), _DailyFetcher("BinanceVisionFetcher")]
    )
    df, source = manager.get_daily_data("BTCUSDT", days=5)

    assert source == "BinanceVisionFetcher"
    assert calls == [("BinanceVisionFetcher", "BTCUSDT")]
    assert df is not None and not df.empty


def test_crypto_daily_routing_falls_back_to_yahoo_crypto() -> None:
    import pandas as pd

    from data_provider.base import DataFetchError, DataFetcherManager

    calls = []

    class _FailingBinance:
        name = "BinanceVisionFetcher"
        priority = 1

        def get_daily_data(self, stock_code, start_date=None, end_date=None, days=30):
            calls.append(self.name)
            raise DataFetchError("[BinanceVision] simulated outage")

    class _YahooCrypto:
        name = "YahooCryptoFetcher"
        priority = 3

        def get_daily_data(self, stock_code, start_date=None, end_date=None, days=30):
            calls.append(self.name)
            return pd.DataFrame(
                {"date": [pd.Timestamp("2024-01-01")], "close": [2.0], "volume": [3.0]}
            )

    manager = DataFetcherManager(fetchers=[_FailingBinance(), _YahooCrypto()])
    df, source = manager.get_daily_data("BTC-USDT", days=5)

    assert source == "YahooCryptoFetcher"
    assert calls == ["BinanceVisionFetcher", "YahooCryptoFetcher"]


def test_crypto_realtime_routing_never_uses_stock_fetchers() -> None:
    from data_provider.base import DataFetcherManager
    from data_provider.realtime_types import RealtimeSource, UnifiedRealtimeQuote

    calls = []

    class _QuoteFetcher:
        def __init__(self, name: str, quote=None) -> None:
            self.name = name
            self.priority = 1
            self._quote = quote

        def get_realtime_quote(self, stock_code):
            calls.append(self.name)
            return self._quote

    crypto_quote = UnifiedRealtimeQuote(
        code="BTC-USDT", price=65000.0, source=RealtimeSource.FALLBACK
    )
    manager = DataFetcherManager(
        fetchers=[
            _QuoteFetcher("EfinanceFetcher", crypto_quote),
            _QuoteFetcher("BinanceVisionFetcher", crypto_quote),
        ]
    )
    quote = manager.get_realtime_quote("BTCUSDT")

    assert quote is not None and quote.price == pytest.approx(65000.0)
    assert calls == ["BinanceVisionFetcher"]

    # A manager with only stock sources must return None instead of calling them.
    calls.clear()
    stock_only = DataFetcherManager(fetchers=[_QuoteFetcher("EfinanceFetcher", crypto_quote)])
    assert stock_only.get_realtime_quote("BTCUSDT", log_final_failure=False) is None
    assert calls == []


# ---------------------------------------------------------------------------
# Trading calendar: 24/7 market
# ---------------------------------------------------------------------------
def test_trading_calendar_treats_crypto_as_always_open() -> None:
    from src.core import trading_calendar

    assert trading_calendar.MARKET_EXCHANGE["crypto"] == "24/7"
    assert trading_calendar.MARKET_TIMEZONE["crypto"] == "UTC"
    assert trading_calendar.get_market_for_stock("BTC-USDT") == "crypto"
    assert trading_calendar.get_market_for_stock("BTCUSDT") == "crypto"

    # Weekend / holiday checks never report a 24/7 market as closed.
    assert trading_calendar.is_market_open("crypto", date(2024, 1, 6)) is True
    assert trading_calendar.is_market_open("crypto", date(2024, 12, 25)) is True
    assert trading_calendar.is_continuous_market("crypto") is True
    assert trading_calendar.is_continuous_market("cn") is False

    ctx = trading_calendar.build_market_phase_context(
        market="crypto", current_time=None
    )
    assert ctx.phase == trading_calendar.MarketPhase.INTRADAY
    assert ctx.is_trading_day is True
    assert ctx.is_market_open_now is True
    assert ctx.is_partial_bar is True
    assert ctx.minutes_to_open is None
    assert ctx.minutes_to_close is None
    # No exchange calendar needed, so no degradation warning either.
    assert "calendar_unavailable" not in ctx.warnings
    assert "unknown_market" not in ctx.warnings


def test_trading_calendar_keeps_stock_semantics() -> None:
    from src.core import trading_calendar

    assert trading_calendar.get_market_for_stock("600519") == "cn"
    assert trading_calendar.get_market_for_stock("AAPL") == "us"
    assert trading_calendar.is_continuous_market("us") is False


def test_trading_day_filter_keeps_crypto_when_stock_markets_closed(monkeypatch) -> None:
    """The daily scheduler must not skip crypto because of a stock calendar."""
    from types import SimpleNamespace

    import main
    from src.core import trading_calendar

    monkeypatch.setattr(trading_calendar, "get_open_markets_today", lambda: {"crypto"})
    config = SimpleNamespace(
        trading_day_check_enabled=True,
        market_review_enabled=False,
        market_review_region="cn",
    )
    args = SimpleNamespace(force_run=False, no_market_review=True)

    codes, region, skip_all = main._compute_trading_day_filter(
        config, args, ["BTC-USDT", "BTCUSDT", "600519"]
    )

    assert codes == ["BTC-USDT", "BTCUSDT"]
    assert region is None
    assert skip_all is False


# ---------------------------------------------------------------------------
# Web / API input acceptance
# ---------------------------------------------------------------------------
def test_api_stock_code_validation_accepts_crypto_without_weakening_stocks() -> None:
    pytest.importorskip("fastapi")
    from fastapi import HTTPException

    from api.v1.endpoints.stocks import (
        _validate_and_normalize_stock_code,
        _watchlist_match_key,
    )

    # crypto pairs are accepted and canonicalized
    assert _validate_and_normalize_stock_code("BTC-USDT") == "BTC-USDT"
    assert _validate_and_normalize_stock_code("btcusdt") == "BTC-USDT"
    assert _validate_and_normalize_stock_code("BTCUSDT") == "BTC-USDT"
    assert _validate_and_normalize_stock_code("ETH-BTC") == "ETH-BTC"
    # known stock formats keep their historical normalization
    assert _validate_and_normalize_stock_code("AAPL") == "AAPL"
    assert _validate_and_normalize_stock_code("600519") == "600519"
    assert _validate_and_normalize_stock_code("00700.HK") == "HK00700"

    # non-pair / non-allowlisted inputs are still rejected
    for bad in ("BTC-XRP", "NOTACOINUSDT", "btc usdt", ""):
        with pytest.raises(HTTPException):
            _validate_and_normalize_stock_code(bad)

    # watchlist add/remove keys converge on the canonical pair
    assert _watchlist_match_key("btc-usdt") == "BTC-USDT"
    assert _watchlist_match_key("BTCUSDT") == "BTC-USDT"
    assert _watchlist_match_key("aapl") == "AAPL"
