# -*- coding: utf-8 -*-
"""Offline tests for :mod:`data_provider.yahoo_crypto_fetcher`.

The Yahoo implementation is reused from ``YfinanceFetcher``; these tests only
cover the crypto symbol mapping and the graceful rate-limit degradation, with
the parent implementation monkeypatched (no network access).
"""

from __future__ import annotations

import pytest

from data_provider.base import DataFetchError, DataSourceUnavailableError
from data_provider.realtime_types import RealtimeSource, UnifiedRealtimeQuote
from data_provider.yahoo_crypto_fetcher import YahooCryptoFetcher, is_yahoo_rate_limit_error
from data_provider.yfinance_fetcher import YfinanceFetcher


class YFRateLimitError(Exception):
    """Stand-in for ``yfinance.exceptions.YFRateLimitError``."""


def test_symbol_mapping_uses_yahoo_crypto_tickers() -> None:
    fetcher = YahooCryptoFetcher()

    assert fetcher.name == "YahooCryptoFetcher"
    assert fetcher.priority == 3
    assert fetcher._convert_stock_code("BTC-USDT") == "BTC-USD"
    assert fetcher._convert_stock_code("btcusdt") == "BTC-USD"
    assert fetcher._convert_stock_code("ETH-BTC") == "ETH-BTC"
    with pytest.raises(DataSourceUnavailableError, match="不是支持的虚拟货币交易对"):
        fetcher._convert_stock_code("AAPL")


def test_rate_limit_detection_walks_the_exception_chain() -> None:
    assert is_yahoo_rate_limit_error(YFRateLimitError("Too Many Requests. Rate limited."))
    assert is_yahoo_rate_limit_error(RuntimeError("HTTP 429 from query1.finance.yahoo.com"))

    wrapped = DataFetchError("Yahoo Finance 获取数据失败")
    wrapped.__cause__ = YFRateLimitError("rate limited")
    assert is_yahoo_rate_limit_error(wrapped) is True

    assert is_yahoo_rate_limit_error(ValueError("unrelated failure")) is False


def test_daily_rate_limit_degrades_to_data_source_unavailable(monkeypatch) -> None:
    def _rate_limited(self, stock_code, start_date, end_date):
        raise DataFetchError("Yahoo Finance 获取数据失败") from YFRateLimitError(
            "Too Many Requests. Rate limited. Try after a while."
        )

    monkeypatch.setattr(YfinanceFetcher, "_fetch_raw_data", _rate_limited)
    fetcher = YahooCryptoFetcher()

    with pytest.raises(DataSourceUnavailableError, match="限流"):
        fetcher._fetch_raw_data("BTC-USDT", "2024-01-01", "2024-01-03")


def test_daily_other_errors_keep_their_existing_semantics(monkeypatch) -> None:
    def _boom(self, stock_code, start_date, end_date):
        raise DataFetchError("Yahoo Finance 未查询到数据")

    monkeypatch.setattr(YfinanceFetcher, "_fetch_raw_data", _boom)
    fetcher = YahooCryptoFetcher()

    with pytest.raises(DataFetchError, match="未查询到数据"):
        fetcher._fetch_raw_data("BTC-USDT", "2024-01-01", "2024-01-03")


def test_realtime_quote_is_returned_with_crypto_market_metadata(monkeypatch) -> None:
    def _quote(self, stock_code):
        return UnifiedRealtimeQuote(
            code=stock_code,
            name="",
            source=RealtimeSource.FALLBACK,
            market="us",
            currency="USD",
            price=65000.0,
            change_pct=1.5,
        )

    monkeypatch.setattr(YfinanceFetcher, "get_realtime_quote", _quote)
    fetcher = YahooCryptoFetcher()

    quote = fetcher.get_realtime_quote("BTCUSDT")

    assert quote is not None
    assert quote.code == "BTC-USDT"
    assert quote.name == "BTC-USDT"
    assert quote.market == "crypto"
    assert quote.currency == "USD"
    assert quote.price == pytest.approx(65000.0)


def test_realtime_quote_none_degrades_with_clear_message(monkeypatch) -> None:
    monkeypatch.setattr(YfinanceFetcher, "get_realtime_quote", lambda self, code: None)
    fetcher = YahooCryptoFetcher()

    with pytest.raises(DataSourceUnavailableError, match="429"):
        fetcher.get_realtime_quote("BTC-USDT")


def test_realtime_quote_skips_non_crypto_symbols(monkeypatch) -> None:
    called = []

    def _quote(self, stock_code):
        called.append(stock_code)
        return None

    monkeypatch.setattr(YfinanceFetcher, "get_realtime_quote", _quote)
    fetcher = YahooCryptoFetcher()

    assert fetcher.get_realtime_quote("600519") is None
    assert called == []


def test_realtime_support_gate_extends_parent_families() -> None:
    fetcher = YahooCryptoFetcher()

    assert fetcher._supports_realtime_quote("BTC-USD") is True
    assert fetcher._supports_realtime_quote("AAPL") is True  # parent US family
    assert fetcher._supports_realtime_quote("600519") is False
