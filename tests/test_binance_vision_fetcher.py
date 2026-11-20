# -*- coding: utf-8 -*-
"""Offline tests for :mod:`data_provider.binance_vision_fetcher`.

The HTTP layer (``_http_get_json``) is monkeypatched with recorded Binance
Vision payloads, so no test performs a network request.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import pytest

import data_provider.binance_vision_fetcher as module
from data_provider.base import DataFetchError, DataSourceUnavailableError
from data_provider.binance_vision_fetcher import BinanceVisionFetcher


# ---------------------------------------------------------------------------
# Recorded payloads (structure copied from /api/v3/klines and /api/v3/ticker/24hr)
# ---------------------------------------------------------------------------
KLINES_2024_01_01_TO_03 = [
    [
        1704067200000,          # openTime 2024-01-01T00:00:00Z
        "42283.58000000",
        "42593.00000000",
        "42138.00000000",
        "42224.08000000",
        "19679.83431000",       # volume (BTC)
        1704153599999,
        "835157583.45121290",   # quote asset volume (USDT)
        412935,
        "9872.18396000",
        "419074304.35123450",
        "0",
    ],
    [
        1704153600000,          # openTime 2024-01-02T00:00:00Z
        "42224.08000000",
        "45900.00000000",
        "42158.00000000",
        "44957.15000000",
        "33601.51700000",
        1704239999999,
        "1487478936.97087500",
        620515,
        "17134.37000000",
        "758298779.18819200",
        "0",
    ],
    [
        1704240000000,          # openTime 2024-01-03T00:00:00Z
        "44957.15000000",
        "45388.00000000",
        "42810.00000000",
        "42847.89000000",
        "29466.22200000",
        1704326399999,
        "1301699630.85541300",
        573298,
        "14698.53500000",
        "649604798.78765900",
        "0",
    ],
]

TICKER_24HR = {
    "symbol": "BTCUSDT",
    "priceChange": "-1467.77000000",
    "priceChangePercent": "-3.318",
    "weightedAvgPrice": "44300.53276244",
    "prevClosePrice": "44241.00000000",
    "lastPrice": "44197.00000000",
    "lastQty": "0.01200000",
    "bidPrice": "44196.99000000",
    "bidQty": "1.13582000",
    "askPrice": "44197.00000000",
    "askQty": "0.32822000",
    "openPrice": "45664.77000000",
    "highPrice": "45943.00000000",
    "lowPrice": "43580.01000000",
    "volume": "21575.24371000",
    "quoteVolume": "955921039.39011400",
    "openTime": 1704326400000,
    "closeTime": 1704412799999,
    "firstId": 3551913429,
    "lastId": 3554104207,
    "count": 2190779,
}


class _Recorder:
    """Records every patched HTTP call and replays queued payloads."""

    def __init__(self, payloads):
        self._payloads = list(payloads)
        self.calls = []

    def __call__(self, url, params, timeout):
        self.calls.append({"url": url, "params": dict(params or {}), "timeout": timeout})
        if not self._payloads:
            return []
        payload = self._payloads.pop(0)
        if isinstance(payload, Exception):
            raise payload
        return payload


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """Never sleep in tests (BaseFetcher helpers could add jitter)."""
    monkeypatch.setattr(BinanceVisionFetcher, "random_sleep", staticmethod(lambda *a, **k: None))


def _patch_http(monkeypatch, payloads) -> _Recorder:
    recorder = _Recorder(payloads)
    monkeypatch.setattr(module, "_http_get_json", recorder)
    return recorder


# ---------------------------------------------------------------------------
# Daily K-lines
# ---------------------------------------------------------------------------
def test_get_daily_data_normalizes_klines(monkeypatch) -> None:
    recorder = _patch_http(monkeypatch, [KLINES_2024_01_01_TO_03])
    fetcher = BinanceVisionFetcher()

    df = fetcher.get_daily_data(
        "BTC-USDT", start_date="2024-01-01", end_date="2024-01-03"
    )

    assert len(recorder.calls) == 1
    request = recorder.calls[0]
    assert request["url"] == "https://data-api.binance.vision/api/v3/klines"
    assert request["params"]["symbol"] == "BTCUSDT"
    assert request["params"]["interval"] == "1d"
    assert request["params"]["limit"] == 1000
    assert request["params"]["startTime"] == int(
        datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp() * 1000
    )
    assert request["params"]["endTime"] > request["params"]["startTime"]
    assert request["timeout"] == pytest.approx(10.0)

    # Standard columns + technical indicators from BaseFetcher
    for column in ("date", "open", "high", "low", "close", "volume", "amount", "pct_chg"):
        assert column in df.columns
    assert df["code"].tolist() == ["BTC-USDT"] * 3
    # BaseFetcher._clean_data parses the date column to datetime.
    assert [pd.Timestamp(value).date() for value in df["date"]] == [
        pd.Timestamp("2024-01-01").date(),
        pd.Timestamp("2024-01-02").date(),
        pd.Timestamp("2024-01-03").date(),
    ]
    assert df["close"].tolist() == pytest.approx([42224.08, 44957.15, 42847.89])
    assert df["high"].tolist() == pytest.approx([42593.0, 45900.0, 45388.0])
    assert df["low"].tolist() == pytest.approx([42138.0, 42158.0, 42810.0])
    assert df["volume"].tolist() == pytest.approx([19679.83431, 33601.517, 29466.222])
    # amount uses Binance quote asset volume
    assert df["amount"].tolist() == pytest.approx(
        [835157583.4512129, 1487478936.970875, 1301699630.855413]
    )
    # first bar has no previous close -> 0, then true pct change
    assert df["pct_chg"].iloc[0] == pytest.approx(0.0)
    assert df["pct_chg"].iloc[1] == pytest.approx(round((44957.15 - 42224.08) / 42224.08 * 100, 2))
    assert df["pct_chg"].iloc[2] == pytest.approx(round((42847.89 - 44957.15) / 44957.15 * 100, 2))


def test_get_daily_data_accepts_concatenated_symbol(monkeypatch) -> None:
    recorder = _patch_http(monkeypatch, [KLINES_2024_01_01_TO_03])
    fetcher = BinanceVisionFetcher()

    df = fetcher.get_daily_data("btcusdt", start_date="2024-01-01", end_date="2024-01-03")

    assert recorder.calls[0]["params"]["symbol"] == "BTCUSDT"
    assert df["code"].iloc[0] == "btcusdt"


def test_get_daily_data_paginates_until_short_page(monkeypatch) -> None:
    page_one = [
        [
            1704067200000 + index * 86_400_000,
            "100.0",
            "101.0",
            "99.0",
            "100.5",
            "10.0",
            1704067200000 + index * 86_400_000 + 86_399_999,
            "1005.0",
            10,
            "5.0",
            "502.5",
            "0",
        ]
        for index in range(1000)
    ]
    page_two = [
        [
            1704067200000 + 1000 * 86_400_000,
            "101.0",
            "102.0",
            "100.0",
            "101.5",
            "11.0",
            1704067200000 + 1000 * 86_400_000 + 86_399_999,
            "1116.5",
            11,
            "5.0",
            "557.5",
            "0",
        ]
    ]
    recorder = _patch_http(monkeypatch, [page_one, page_two])
    fetcher = BinanceVisionFetcher()

    df = fetcher._fetch_raw_data("BTC-USDT", "2023-12-25", "2026-12-31")

    assert len(recorder.calls) == 2
    assert recorder.calls[1]["params"]["startTime"] == 1704067200000 + 1000 * 86_400_000
    assert len(df) == 1001


def test_non_200_raises_data_source_unavailable(monkeypatch) -> None:
    def _boom(url, params, timeout):
        raise DataSourceUnavailableError("[BinanceVision] HTTP 451 for " + url)

    monkeypatch.setattr(module, "_http_get_json", _boom)
    fetcher = BinanceVisionFetcher()

    with pytest.raises(DataSourceUnavailableError, match="HTTP 451"):
        fetcher._fetch_raw_data("BTC-USDT", "2024-01-01", "2024-01-03")


def test_malformed_payload_raises_data_source_unavailable(monkeypatch) -> None:
    _patch_http(monkeypatch, [{"code": -1121, "msg": "Invalid symbol."}])
    fetcher = BinanceVisionFetcher()

    with pytest.raises(DataSourceUnavailableError, match="K 线返回结构"):
        fetcher._fetch_raw_data("BTC-USDT", "2024-01-01", "2024-01-03")


def test_malformed_kline_row_raises_data_source_unavailable(monkeypatch) -> None:
    _patch_http(monkeypatch, [[[1704067200000, "1.0", "2.0"]]])
    fetcher = BinanceVisionFetcher()

    with pytest.raises(DataSourceUnavailableError, match="K 线结构异常"):
        fetcher._fetch_raw_data("BTC-USDT", "2024-01-01", "2024-01-03")


def test_provider_errors_surface_as_data_fetch_error_via_base(monkeypatch) -> None:
    """BaseFetcher keeps its historical wrapping contract for daily data."""
    def _boom(url, params, timeout):
        raise DataSourceUnavailableError("[BinanceVision] HTTP 500 for " + url)

    monkeypatch.setattr(module, "_http_get_json", _boom)
    fetcher = BinanceVisionFetcher()

    with pytest.raises(DataFetchError, match="HTTP 500"):
        fetcher.get_daily_data("BTC-USDT", start_date="2024-01-01", end_date="2024-01-03")


def test_empty_page_returns_empty_daily_result(monkeypatch) -> None:
    _patch_http(monkeypatch, [[]])
    fetcher = BinanceVisionFetcher()

    # BaseFetcher raises DataFetchError for an empty result instead of crashing.
    with pytest.raises(DataFetchError, match="未获取到"):
        fetcher.get_daily_data("BTC-USDT", start_date="2024-01-01", end_date="2024-01-03")


def test_non_crypto_symbol_never_hits_http(monkeypatch) -> None:
    recorder = _patch_http(monkeypatch, [KLINES_2024_01_01_TO_03])
    fetcher = BinanceVisionFetcher()

    with pytest.raises(DataSourceUnavailableError, match="不是支持的虚拟货币交易对"):
        fetcher._fetch_raw_data("AAPL", "2024-01-01", "2024-01-03")
    assert recorder.calls == []


# ---------------------------------------------------------------------------
# Realtime quote
# ---------------------------------------------------------------------------
def test_get_realtime_quote_maps_24hr_ticker(monkeypatch) -> None:
    recorder = _patch_http(monkeypatch, [TICKER_24HR])
    fetcher = BinanceVisionFetcher()

    quote = fetcher.get_realtime_quote("BTCUSDT")

    assert recorder.calls[0]["url"] == "https://data-api.binance.vision/api/v3/ticker/24hr"
    assert recorder.calls[0]["params"] == {"symbol": "BTCUSDT"}
    assert quote is not None
    assert quote.code == "BTC-USDT"
    assert quote.market == "crypto"
    assert quote.currency == "USD"  # USDT stablecoin pair is reported as USD
    assert quote.price == pytest.approx(44197.0)
    assert quote.change_pct == pytest.approx(-3.32)  # rounded to 2 decimals
    assert quote.change_amount == pytest.approx(-1467.77)
    assert quote.pre_close == pytest.approx(44241.0)
    assert quote.open_price == pytest.approx(45664.77)
    assert quote.high == pytest.approx(45943.0)
    assert quote.low == pytest.approx(43580.01)
    assert quote.volume == 21575
    assert quote.amount == pytest.approx(955921039.390114)
    assert quote.amplitude == pytest.approx(round((45943.0 - 43580.01) / 44241.0 * 100, 2))
    assert quote.provider_timestamp == datetime.fromtimestamp(
        1704412799999 / 1000.0, tz=timezone.utc
    ).isoformat()
    assert quote.has_basic_data() is True
    # Fields the provider does not supply stay None.
    assert quote.pe_ratio is None
    assert quote.pb_ratio is None
    assert quote.turnover_rate is None
    assert quote.total_mv is None


def test_get_realtime_quote_rejects_malformed_ticker(monkeypatch) -> None:
    _patch_http(monkeypatch, [{"symbol": "BTCUSDT"}])
    fetcher = BinanceVisionFetcher()

    with pytest.raises(DataSourceUnavailableError, match="lastPrice"):
        fetcher.get_realtime_quote("BTC-USDT")


def test_get_realtime_quote_rejects_non_dict_payload(monkeypatch) -> None:
    _patch_http(monkeypatch, [KLINES_2024_01_01_TO_03])
    fetcher = BinanceVisionFetcher()

    with pytest.raises(DataSourceUnavailableError, match="24hr ticker"):
        fetcher.get_realtime_quote("BTC-USDT")


def test_env_configuration_is_read(monkeypatch) -> None:
    monkeypatch.setenv("BINANCE_VISION_TIMEOUT_SEC", "3.5")
    fetcher = BinanceVisionFetcher()
    assert fetcher.timeout == pytest.approx(3.5)

    def _boom(url, params, timeout):
        assert timeout == pytest.approx(3.5)
        raise DataSourceUnavailableError("stop")

    monkeypatch.setattr(module, "_http_get_json", _boom)
    with pytest.raises(DataSourceUnavailableError):
        fetcher.get_realtime_quote("BTC-USDT")
