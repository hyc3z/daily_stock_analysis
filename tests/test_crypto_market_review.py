# -*- coding: utf-8 -*-
"""Offline tests for the 24/7 crypto market-review region.

Covers:
* region parsing/validation accepting ``crypto`` (single value and comma
  subsets) while ``both`` keeps its six-stock-market meaning;
* calendar gating: crypto is never skipped because stock markets are closed;
* prompt/market context: 24/7 framing, explicit absence of limit-up/northbound/
  dragon-tiger data, Binance Vision-only data block;
* fail-open: a Binance Vision outage yields an explicit no-data section and
  never raises, without breaking other regions.

No network: every HTTP call is monkeypatched at
``data_provider.binance_vision_fetcher._http_get_json``.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import data_provider.binance_vision_fetcher as bvf
from src.core import trading_calendar
from src.core.crypto_market_review import (
    CRYPTO_REVIEW_PAIRS,
    CryptoReviewData,
    load_crypto_review_data,
    render_crypto_market_data_block,
    render_crypto_no_data_report,
    render_crypto_prompt_requirements,
    render_crypto_review_report,
)
from src.market_analyzer import MarketAnalyzer
from src.utils.market_review_region import (
    CRYPTO_MARKET_REVIEW_REGION,
    MARKET_REVIEW_REGION_ALL,
    MARKET_REVIEW_REGION_SET,
    normalize_market_review_region_lenient,
    normalize_market_review_region_strict,
)

_TICKER_PAYLOAD = {
    "symbol": "BTCUSDT",
    "priceChange": "1500.00000000",
    "priceChangePercent": "2.350",
    "prevClosePrice": "63000.00000000",
    "openPrice": "63500.00000000",
    "highPrice": "65500.00000000",
    "lowPrice": "62800.00000000",
    "lastPrice": "64500.00000000",
    "volume": "12000",
    "quoteVolume": "780000000.00000000",
    "closeTime": 1700000000000,
}

_KLINES_PAYLOAD = [
    # openTime, open, high, low, close, volume, closeTime, quoteVolume, trades, takerBase, takerQuote, ignore
    [1699920000000, "63000", "65000", "62500", "64500", "1000", 1700006399999, "64000000", 100, "500", "32000000", "0"],
    [1700006400000, "64500", "66000", "64000", "65500", "1100", 1700092799999, "71500000", 100, "520", "33000000", "0"],
]


def _install_fake_http(monkeypatch, *, ticker=None, klines=None):
    """Route the fetcher's HTTP layer to fixed payloads (offline)."""

    def fake_http_get_json(url, params=None, timeout=None):
        if url.endswith("/ticker/24hr"):
            payload = dict(ticker or _TICKER_PAYLOAD)
            payload["symbol"] = (params or {}).get("symbol") or payload["symbol"]
            return payload
        return list(klines if klines is not None else _KLINES_PAYLOAD)

    monkeypatch.setattr(bvf, "_http_get_json", fake_http_get_json)


def _install_outage(monkeypatch):
    def boom(url, params=None, timeout=None):
        raise RuntimeError("simulated Binance Vision outage")

    monkeypatch.setattr(bvf, "_http_get_json", boom)


def _config(**overrides):
    values = {
        "report_language": "zh",
        "market_review_color_scheme": "green_up",
        "market_review_region": "crypto",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


# ---------------------------------------------------------------------------
# Region parsing / validation
# ---------------------------------------------------------------------------
def test_crypto_is_a_valid_market_review_region() -> None:
    assert CRYPTO_MARKET_REVIEW_REGION == "crypto"
    assert "crypto" in MARKET_REVIEW_REGION_SET
    assert normalize_market_review_region_lenient("crypto") == "crypto"
    assert normalize_market_review_region_lenient("CRYPTO") == "crypto"
    assert normalize_market_review_region_strict("crypto") == "crypto"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("cn,crypto", "cn,crypto"),
        ("crypto,us", "us,crypto"),
        ("crypto,crypto", "crypto"),
    ],
)
def test_region_comma_subsets_accept_crypto(raw: str, expected: str) -> None:
    assert normalize_market_review_region_lenient(raw) == expected
    assert normalize_market_review_region_strict(raw) == expected


def test_lenient_region_parser_filters_unknown_tokens_but_keeps_crypto() -> None:
    assert normalize_market_review_region_lenient("cn,eu,us,crypto") == "cn,us,crypto"


def test_both_keeps_its_six_stock_market_meaning() -> None:
    assert MARKET_REVIEW_REGION_ALL == "cn,hk,us,jp,kr,tw"
    assert normalize_market_review_region_lenient("both") == "cn,hk,us,jp,kr,tw"
    assert normalize_market_review_region_strict("both") == "cn,hk,us,jp,kr,tw"
    assert "crypto" not in normalize_market_review_region_lenient("both").split(",")


def test_unknown_region_tokens_still_fail_fast() -> None:
    for raw in ("", "eu", "crypto,eu", "us,,crypto"):
        with pytest.raises(ValueError):
            normalize_market_review_region_strict(raw)


# ---------------------------------------------------------------------------
# 24/7 calendar semantics
# ---------------------------------------------------------------------------
def test_crypto_region_is_never_gated_by_stock_calendars() -> None:
    # every stock market closed, crypto requested -> still runs crypto
    assert trading_calendar.compute_effective_region("crypto", set()) == "crypto"
    assert trading_calendar.compute_effective_region("cn,crypto", set()) == "crypto"
    # mixed run keeps the open stock market plus crypto, canonical order intact
    assert trading_calendar.compute_effective_region("crypto,us", {"us"}) == "us,crypto"


def test_stock_region_gating_is_unchanged() -> None:
    assert trading_calendar.compute_effective_region("both", {"cn", "us"}) == "cn,us"
    assert trading_calendar.compute_effective_region("cn", {"hk"}) == ""
    assert trading_calendar.compute_effective_region("invalid", {"cn"}) == "cn"


# ---------------------------------------------------------------------------
# Data loading (Binance Vision only, fail-open)
# ---------------------------------------------------------------------------
def test_load_crypto_review_data_uses_btc_eth_tickers_and_klines(monkeypatch) -> None:
    _install_fake_http(monkeypatch)

    data = load_crypto_review_data()

    assert data.available is True
    assert [quote["code"] for quote in data.quotes] == list(CRYPTO_REVIEW_PAIRS)
    assert data.quotes[0]["current"] == pytest.approx(64500.0)
    assert data.quotes[0]["change_pct"] == pytest.approx(2.35)
    assert data.quotes[0]["amount"] == pytest.approx(780000000.0)
    assert data.errors == []
    assert set(data.klines) == set(CRYPTO_REVIEW_PAIRS)
    assert data.klines["BTC-USDT"][-1]["close"] == pytest.approx(65500.0)
    assert data.klines["BTC-USDT"][-1]["pct_chg"] == pytest.approx(1.55)


def test_load_crypto_review_data_is_fail_open(monkeypatch) -> None:
    _install_outage(monkeypatch)

    data = load_crypto_review_data()

    assert data.available is False
    assert data.quotes == []
    assert data.klines == {}
    assert data.errors


def test_crypto_overview_and_report_with_data(monkeypatch) -> None:
    _install_fake_http(monkeypatch)
    analyzer = MarketAnalyzer(region="crypto", config=_config())

    overview = analyzer.get_market_overview()
    report = analyzer.generate_market_review(overview, [])

    assert overview.crypto_context["available"] is True
    assert overview.crypto_context["source"] == "Binance Vision"
    assert [index.code for index in overview.indices] == list(CRYPTO_REVIEW_PAIRS)
    assert "虚拟货币" in report
    assert "BTC" in report and "ETH" in report
    # stock-only data is never requested or rendered
    assert overview.top_sectors == [] and overview.top_concepts == []
    assert overview.up_count == 0 and overview.limit_up_count == 0
    assert "涨停家数" not in report


# ---------------------------------------------------------------------------
# Prompt / market context
# ---------------------------------------------------------------------------
def test_prompt_states_24x7_and_forbidden_stock_framing(monkeypatch) -> None:
    _install_fake_http(monkeypatch)
    analyzer = MarketAnalyzer(region="crypto", config=_config())

    overview = analyzer.get_market_overview()
    prompt = analyzer._build_review_prompt(overview, [])

    assert "24/7" in prompt
    assert "涨跌停" in prompt and "北向资金" in prompt and "龙虎榜" in prompt
    assert "资金费率" in prompt and "链上" in prompt
    assert "Binance Vision" in prompt
    # no invented stock-market data blocks
    assert "Market Breadth" not in prompt
    assert "Sector / Theme Performance" not in prompt
    # the data block carries the 24h ticker values, not index levels
    assert "64,500.00" in prompt
    assert "BTC-USDT" in prompt


def test_prompt_states_absent_data_for_english(monkeypatch) -> None:
    _install_fake_http(monkeypatch)
    analyzer = MarketAnalyzer(region="crypto", config=_config(report_language="en"))

    prompt = analyzer._build_review_prompt(analyzer.get_market_overview(), [])

    assert "24/7" in prompt
    assert "no limit-up/limit-down" in prompt
    assert "Northbound" in prompt and "Dragon-Tiger" in prompt
    assert "Binance Vision" in prompt


def test_crypto_prompt_rules_are_shared_constants() -> None:
    zh = render_crypto_prompt_requirements("zh")
    en = render_crypto_prompt_requirements("en")

    assert "24/7" in zh and "24/7" in en
    for blocked in ("涨停", "北向资金", "龙虎榜"):
        assert blocked in zh
    for blocked in ("limit-up", "Northbound", "Dragon-Tiger"):
        assert blocked in en
    assert "编造" in zh and "invent" in en


def test_market_data_block_and_report_render_24h_values() -> None:
    data = CryptoReviewData(
        quotes=[{
            "code": "BTC-USDT", "name": "BTC", "current": 64500.0, "change": 1500.0,
            "change_pct": 2.35, "open": 63500.0, "high": 65500.0, "low": 62800.0,
            "prev_close": 63000.0, "volume": 12000.0, "amount": 780000000.0, "amplitude": 4.29,
        }],
        klines={"BTC-USDT": [{"date": "2023-11-15", "close": 65500.0, "pct_chg": 1.55, "amount": 71500000.0}]},
        as_of="2023-11-15T00:00:00+00:00",
    )

    block = render_crypto_market_data_block(data, "zh")
    report = render_crypto_review_report(data, date="2023-11-15", language="zh")

    assert "64,500.00" in block and "0.78" in block
    assert "2023-11-15" in report and "64,500.00" in report
    assert "24/7" in report
    assert "无涨停/跌停" in report


# ---------------------------------------------------------------------------
# Fail-open behaviour
# ---------------------------------------------------------------------------
def test_binance_outage_yields_explicit_no_data_review(monkeypatch) -> None:
    _install_outage(monkeypatch)
    analyzer = MarketAnalyzer(region="crypto", config=_config())

    overview = analyzer.get_market_overview()
    assert overview.crypto_context["available"] is False
    assert overview.indices == []
    assert overview.crypto_context["errors"]

    # no exception, explicit "no data" section, no invented numbers
    report = analyzer.generate_market_review(overview, [])
    assert "暂无数据" in report
    assert "Binance Vision" in report
    assert "64,500" not in report


def test_no_data_report_builder_is_language_aware() -> None:
    zh = render_crypto_no_data_report(date="2023-11-15", language="zh", errors=["boom"])
    en = render_crypto_no_data_report(date="2023-11-15", language="en", errors=["boom"])

    assert "暂无数据" in zh and "boom" in zh
    assert "No data available" in en and "boom" in en


def test_crypto_failure_does_not_break_other_regions(monkeypatch) -> None:
    """A raising crypto region must still let the stock regions render."""
    from src.core import market_review

    _install_outage(monkeypatch)
    calls = []

    class _FakeAnalyzer:
        def __init__(self, *, region, **_kwargs):
            self.region = region

        def run_daily_review_with_snapshot(self):
            calls.append(self.region)
            if self.region == "crypto":
                raise RuntimeError("simulated crypto region crash")
            return SimpleNamespace(
                report=f"{self.region} recap",
                structured_payload={},
                market_light_snapshot=None,
            )

    monkeypatch.setattr(market_review, "MarketAnalyzer", _FakeAnalyzer)

    review_result, report = market_review._run_region_review(
        region="crypto",
        search_service=None,
        analyzer=None,
        config=_config(),
        trigger_source="test",
        query_id="q",
    )
    assert review_result is None
    assert "暂无数据" in report
    assert "simulated crypto region crash" in report

    stock_result, stock_report = market_review._run_region_review(
        region="cn",
        search_service=None,
        analyzer=None,
        config=_config(market_review_region="cn"),
        trigger_source="test",
        query_id="q",
    )
    assert stock_result is not None
    assert stock_report == "cn recap"
    assert calls == ["crypto", "cn"]


def test_market_review_region_titles_include_crypto() -> None:
    from src.core.market_review import (
        _MARKET_REVIEW_MARKETS,
        _get_market_review_market_heading,
        _get_market_review_text,
    )

    assert [market for market, _key, _label in _MARKET_REVIEW_MARKETS][-1] == "crypto"
    for language in ("zh", "en", "ko"):
        assert _get_market_review_text(language)["crypto_title"].startswith("#")
    assert "虚拟货币" in _get_market_review_market_heading("zh", "crypto")
    assert "Virtual-Currency" in _get_market_review_market_heading("en", "crypto")
