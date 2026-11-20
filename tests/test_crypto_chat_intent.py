# -*- coding: utf-8 -*-
"""Offline tests for crypto acceptance in the chat / intent layer.

Covers the conversational side of virtual-currency support (the symbol layer
itself is covered by ``tests/test_crypto_symbols.py``):

* pair forms (``BTC-USDT`` / ``BTCUSDT``) and the documented Chinese aliases
  resolve to a canonical crypto analysis target in the intent pipeline;
* the chat stock-scope layer claims a crypto mention instead of shredding it
  into two bogus US tickers;
* tool arguments normalize to the canonical pair;
* stock parsing is untouched (``600519`` / ``AAPL`` / ``茅台``).

No network access: every assertion is pure string/DB logic.
"""

from __future__ import annotations

import pytest

from src.agent.stock_scope import (
    extract_stock_code_spans,
    extract_stock_codes,
    resolve_stock_scope,
)
from src.agent.tools.execution import _normalize_tool_stock_code
from src.agent.web_intent_resolver import WebIntentResolver, _merge_llm_result
from src.agent.web_intent_tokenizer import _identify_stock_codes, _preprocess_text
from src.agent.web_intent_types import WebIntent, WebIntentResolution
from src.services.market_symbol_utils import (
    CRYPTO_BASE_ASSETS,
    CRYPTO_NAME_ALIASES,
    crypto_alias_pair,
    crypto_pair_from_base,
    iter_crypto_chat_spans,
    resolve_crypto_chat_symbol,
)

CRYPTO_MARKET = "crypto"


def _tokens(message: str):
    return _identify_stock_codes(_preprocess_text(message)[1])


# ---------------------------------------------------------------------------
# Shared helpers: alias map + pair resolution
# ---------------------------------------------------------------------------
def test_alias_map_is_small_and_documented() -> None:
    assert CRYPTO_NAME_ALIASES == {
        "比特币": "BTC",
        "以太坊": "ETH",
        "以太币": "ETH",
        "狗狗币": "DOGE",
        "索拉纳": "SOL",
    }
    # every alias must point at an allowlisted base asset (no invented coins)
    assert set(CRYPTO_NAME_ALIASES.values()) <= set(CRYPTO_BASE_ASSETS)


@pytest.mark.parametrize(
    "raw",
    ["BTC-USDT", "btc-usdt", "BTCUSDT", "btcusdt", " Btc-Usdt "],
)
def test_pair_forms_resolve_to_canonical_pair(raw: str) -> None:
    assert resolve_crypto_chat_symbol(raw) == "BTC-USDT"


@pytest.mark.parametrize(
    "alias,expected",
    [
        ("比特币", "BTC-USDT"),
        ("以太坊", "ETH-USDT"),
        ("以太币", "ETH-USDT"),
        ("狗狗币", "DOGE-USDT"),
        ("索拉纳", "SOL-USDT"),
    ],
)
def test_chinese_aliases_resolve_to_default_pair(alias: str, expected: str) -> None:
    assert crypto_alias_pair(alias) == expected
    assert resolve_crypto_chat_symbol(alias) == expected


def test_unknown_names_and_quotes_stay_unresolved() -> None:
    assert resolve_crypto_chat_symbol("空气币") is None
    assert resolve_crypto_chat_symbol("BTC-XRP") is None
    assert resolve_crypto_chat_symbol("NOTACOINUSDT") is None
    assert crypto_pair_from_base("NOTACOIN") is None
    # bare base assets stay out of the strict (non-chat) resolution path
    assert resolve_crypto_chat_symbol("BTC") is None


def test_chat_spans_cover_pairs_and_aliases() -> None:
    assert iter_crypto_chat_spans("分析BTC-USDT走势") == [(2, 10, "BTC-USDT")]
    assert iter_crypto_chat_spans("BTCUSDT怎么样") == [(0, 7, "BTC-USDT")]
    assert iter_crypto_chat_spans("看看比特币和以太坊") == [
        (2, 5, "BTC-USDT"),
        (6, 9, "ETH-USDT"),
    ]


# ---------------------------------------------------------------------------
# Chat stock scope: crypto mentions become one analysis target
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "message",
    ["分析BTC-USDT", "分析BTCUSDT", "分析btc-usdt", "看一下BTCUSDT的走势"],
)
def test_stock_scope_extracts_crypto_pair_as_single_target(message: str) -> None:
    assert extract_stock_codes(message) == ["BTC-USDT"]
    spans = extract_stock_code_spans(message)
    assert [token for _start, _end, token in spans] == ["BTC-USDT"]


def test_stock_scope_recovers_bare_base_and_aliases() -> None:
    # "BTC" is not a listed ticker, so the chat layer may treat it as BTC-USDT
    assert extract_stock_codes("BTC怎么样") == ["BTC-USDT"]
    assert extract_stock_codes("比特币怎么样") == ["BTC-USDT"]
    assert extract_stock_codes("以太坊和狗狗币") == ["ETH-USDT", "DOGE-USDT"]


def test_stock_scope_switches_from_stock_to_crypto() -> None:
    resolution = resolve_stock_scope("分析BTCUSDT", {"stock_code": "600519"})

    assert resolution.stock_scope is not None
    assert resolution.stock_scope.mode == "switch"
    assert resolution.stock_scope.expected_stock_code == "BTC-USDT"
    assert resolution.effective_context["stock_code"] == "BTC-USDT"


def test_chat_stock_identity_accepts_crypto_pair() -> None:
    from src.agent.stock_scope import StockIdentity
    from src.services.stock_list_parser import default_index_registry

    identity = StockIdentity.from_code("BTC-USDT", default_index_registry())

    assert identity.stock_code == "BTC-USDT"
    assert identity.canonical_id == "BTC-USDT"
    assert identity.asset_type == "stock"


def test_tool_arguments_normalize_to_canonical_pair() -> None:
    assert _normalize_tool_stock_code("BTCUSDT") == "BTC-USDT"
    assert _normalize_tool_stock_code("btc-usdt") == "BTC-USDT"
    assert _normalize_tool_stock_code("比特币") == "BTC-USDT"
    assert _normalize_tool_stock_code("BTC") == "BTC-USDT"


# ---------------------------------------------------------------------------
# Intent pipeline: crypto is a normal analysis target
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "message",
    ["分析BTC-USDT", "分析BTCUSDT", "比特币怎么样", "以太坊怎么看"],
)
def test_tokenizer_tags_crypto_as_stock_code(message: str) -> None:
    tokens = _tokens(message)
    crypto_tokens = [t for t in tokens if t.tag == "stock_code"]
    assert len(crypto_tokens) == 1
    stock = crypto_tokens[0].stocks[0]
    assert stock.market == CRYPTO_MARKET
    assert stock.code.endswith("-USDT")
    assert stock.code == stock.code.upper()


@pytest.mark.parametrize(
    "message,expected_code",
    [
        ("分析BTCUSDT", "BTC-USDT"),
        ("比特币多少钱", "BTC-USDT"),
        ("以太币走势分析", "ETH-USDT"),
        ("狗狗币怎么看", "DOGE-USDT"),
        ("索拉纳多少钱", "SOL-USDT"),
    ],
)
def test_resolver_returns_crypto_target(message: str, expected_code: str) -> None:
    resolution = WebIntentResolver().resolve_first(message)

    assert resolution.intent in {WebIntent.STOCK_ANALYSIS, WebIntent.QUOTE_LOOKUP}
    assert [stock.code for stock in resolution.stocks] == [expected_code]
    assert resolution.primary_stock_code == expected_code
    assert resolution.stocks[0].market == CRYPTO_MARKET
    assert resolution.unresolved_names == []


def test_resolver_marks_quote_question_as_quote_lookup() -> None:
    assert WebIntentResolver().resolve_first("BTC多少钱").intent == WebIntent.QUOTE_LOOKUP
    assert WebIntentResolver().resolve_first("分析比特币走势").intent == WebIntent.STOCK_ANALYSIS


def test_llm_merge_accepts_claimed_crypto_pair() -> None:
    rule = WebIntentResolution(
        intent=WebIntent.STOCK_ANALYSIS,
        confidence=0.9,
        stocks=[],
        tokens=["BTCUSDT"],
    )
    # rule-side entity claim is what the gate reads
    rule.stocks = [_crypto_stock("BTC-USDT")]

    merged = _merge_llm_result(
        rule,
        {"intent": "stock_analysis", "confidence": 0.9, "stock_code": "BTCUSDT",
         "message_text": "分析BTCUSDT"},
        {},
    )

    assert [stock.code for stock in merged.stocks] == ["BTC-USDT"]
    assert merged.unresolved_names == []
    assert merged.unverified_codes == []


def test_llm_merge_does_not_invent_crypto_targets() -> None:
    """An unclaimed pair from the LLM must not become a confirmed target."""
    rule = WebIntentResolution(
        intent=WebIntent.STOCK_ANALYSIS, confidence=0.9, tokens=["分析茅台"],
    )

    merged = _merge_llm_result(
        rule,
        {"intent": "stock_analysis", "confidence": 0.9, "stock_code": "ETHUSDT",
         "message_text": "分析茅台"},
        {},
    )

    assert merged.stocks == []
    assert merged.unresolved_names == []


def _crypto_stock(pair: str):
    from src.services.name_to_code_resolver import Stock

    return Stock(code=pair, name=pair, market=CRYPTO_MARKET)


# ---------------------------------------------------------------------------
# Stock regression: crypto support must not touch stock parsing
# ---------------------------------------------------------------------------
def test_stock_codes_keep_their_existing_paths() -> None:
    assert extract_stock_codes("分析600519") == ["600519"]
    assert extract_stock_codes("分析AAPL") == ["AAPL"]
    assert extract_stock_codes("HK00700") == ["HK00700"]
    assert extract_stock_code_spans("分析600519和AAPL") == [
        (2, 8, "600519"),
        (9, 13, "AAPL"),
    ]


def test_stock_tokenizer_and_resolver_are_unchanged() -> None:
    stock_tokens = [t for t in _tokens("分析AAPL") if t.tag == "stock_code"]
    assert stock_tokens[0].stocks[0].code == "AAPL"
    assert stock_tokens[0].stocks[0].market == "us"

    a_share = WebIntentResolver().resolve_first("600519多少钱")
    assert a_share.intent == WebIntent.QUOTE_LOOKUP
    assert a_share.primary_stock_code == "600519"

    us = WebIntentResolver().resolve_first("AAPL多少钱")
    assert us.primary_stock_code == "AAPL"
    assert us.stocks[0].market == "us"

    named = WebIntentResolver().resolve_first("分析茅台")
    assert named.primary_stock_code == "600519"


def test_stock_switch_semantics_are_unchanged() -> None:
    resolution = resolve_stock_scope("分析AAPL", {"stock_code": "600519"})

    assert resolution.stock_scope is not None
    assert resolution.stock_scope.mode == "switch"
    assert resolution.stock_scope.expected_stock_code == "AAPL"
    assert resolution.effective_context["stock_code"] == "AAPL"


def test_tool_normalization_keeps_stock_codes() -> None:
    assert _normalize_tool_stock_code("AAPL") == "AAPL"
    assert _normalize_tool_stock_code("600519") == "600519"
    assert _normalize_tool_stock_code("HK00700") == "HK00700"
