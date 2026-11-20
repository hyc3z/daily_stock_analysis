# -*- coding: utf-8 -*-
"""Shared market-symbol helpers for suffix-only offshore markets and crypto.

Keep this module dependency-light so it can be used by data providers, market
context, trading calendars, stock-index loading, and API input normalization
without introducing import cycles.

Two symbol families live here:

* suffix-only Yahoo markets (``7203.T`` / ``005930.KS`` / ``2330.TW``), and
* virtual-currency pairs (``BTC-USDT`` / ``BTCUSDT``), which resolve to the
  canonical ``BASE-QUOTE`` form through the allowlists below.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class SuffixMarketSpec:
    """A suffix-only Yahoo Finance market rule."""

    market: str
    suffixes: tuple[str, ...]
    digit_lengths: tuple[int, ...]


_SUFFIX_MARKET_SPECS: tuple[SuffixMarketSpec, ...] = (
    SuffixMarketSpec("jp", ("T",), (4, 5)),
    SuffixMarketSpec("kr", ("KS", "KQ"), (6,)),
    # Taiwan support mirrors the same suffix-only pattern; keep it here so the
    # shared helpers stay complete for all yfinance-only offshore markets.
    SuffixMarketSpec("tw", ("TW", "TWO"), (4, 5, 6)),
)

_MARKET_TO_SPEC = {spec.market: spec for spec in _SUFFIX_MARKET_SPECS}
_SUFFIX_TO_SPEC = {
    suffix: spec
    for spec in _SUFFIX_MARKET_SPECS
    for suffix in spec.suffixes
}


def split_suffix_symbol(stock_code: str) -> tuple[str, str] | None:
    """Return ``(base, suffix)`` for dotted symbols, upper-cased and stripped."""

    code = (stock_code or "").strip().upper()
    if "." not in code:
        return None
    base, suffix = code.rsplit(".", 1)
    if not base or not suffix:
        return None
    return base, suffix


def get_suffix_market(stock_code: str) -> Optional[str]:
    """Return jp/kr/tw for supported suffix-only Yahoo symbols, else None."""

    parts = split_suffix_symbol(stock_code)
    if parts is None:
        return None
    base, suffix = parts
    spec = _SUFFIX_TO_SPEC.get(suffix)
    if spec is None:
        return None
    if not (base.isdigit() and len(base) in spec.digit_lengths):
        return None
    return spec.market


def is_suffix_market_symbol(stock_code: str, market: Optional[str] = None) -> bool:
    """Return whether a stock code is a supported suffix-only Yahoo symbol."""

    detected = get_suffix_market(stock_code)
    if market is None:
        return detected is not None
    return detected == (market or "").strip().lower()


def is_jp_suffix_symbol(stock_code: str) -> bool:
    return is_suffix_market_symbol(stock_code, "jp")


def is_kr_suffix_symbol(stock_code: str) -> bool:
    return is_suffix_market_symbol(stock_code, "kr")


def is_tw_suffix_symbol(stock_code: str) -> bool:
    return is_suffix_market_symbol(stock_code, "tw")


def normalize_suffix_market_symbol(stock_code: str) -> Optional[str]:
    """Normalize supported suffix-only symbols to upper-case Yahoo form."""

    parts = split_suffix_symbol(stock_code)
    if parts is None:
        return None
    base, suffix = parts
    if get_suffix_market(f"{base}.{suffix}") is None:
        return None
    return f"{base}.{suffix}"


def suffix_base_lookup_allowed(canonical_code: str) -> bool:
    """Return True when a suffix-market code may be resolved from its bare base.

    JP/KR intentionally allow stock-index-backed bare-code lookup to support the
    existing MVP behavior. TW remains strict suffix-only for now because its
    follow-up index work is not part of this issue.
    """

    return get_suffix_market(canonical_code) in {"jp", "kr"}


def market_suffixes(market: str) -> tuple[str, ...]:
    spec = _MARKET_TO_SPEC.get((market or "").strip().lower())
    return spec.suffixes if spec else ()


# ---------------------------------------------------------------------------
# Virtual-currency (crypto) symbol helpers.
# ---------------------------------------------------------------------------
# Any new market has to stay dependency-light and offline-deterministic: these
# helpers only do string normalization + allowlist lookups, no provider calls.
CRYPTO_MARKET = "crypto"

# Well-known base assets. Only ``allowlisted base + allowlisted quote`` counts
# as crypto, which keeps bare US tickers (``AAPL``/``SHOP``) on the stock path.
CRYPTO_BASE_ASSETS: tuple[str, ...] = (
    "BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "ADA", "TON", "AVAX", "LINK",
    "DOT", "TRX", "LTC", "BCH", "SHIB", "SUI", "APT", "ARB", "OP", "NEAR",
    "ATOM", "FIL", "INJ", "SEI", "TIA", "PEPE", "WIF", "UNI", "AAVE", "ETC",
    "XLM", "HBAR", "ICP", "RENDER", "FET", "AR", "IMX", "STX", "GRT", "THETA",
    "ALGO", "VET", "SAND", "MANA", "AXS", "EOS", "FLOW", "KSM", "ONE", "ZIL",
    "IOTA", "NEO", "DASH", "XMR", "EGLD", "WLD", "ONDO", "JUP", "LDO", "CRV",
    "MKR",
)

# Quote assets accepted in the canonical ``BASE-QUOTE`` form.
CRYPTO_QUOTE_ASSETS: tuple[str, ...] = (
    "USDT", "USDC", "BUSD", "FDUSD", "USD", "BTC", "ETH",
)

# Stablecoin quote -> the currency reported on quotes/daily bars.
CRYPTO_STABLECOIN_QUOTES: frozenset[str] = frozenset({"USDT", "USDC", "BUSD", "FDUSD", "USD"})

_BASE_ASSET_SET = frozenset(CRYPTO_BASE_ASSETS)
_QUOTE_ASSET_SET = frozenset(CRYPTO_QUOTE_ASSETS)

# Longest allowlisted base (``RENDER``) determines the greedy scan window.
_MAX_BASE_LEN = max(len(base) for base in CRYPTO_BASE_ASSETS)


def split_crypto_symbol(code: str) -> tuple[str, str] | None:
    """Return ``(base, quote)`` for a crypto pair, or ``None`` when not crypto.

    Accepts ``BASE-QUOTE`` (``BTC-USDT`` / ``btc-usdt``), ``BASEQUOTE``
    (``BTCUSDT``) and the same forms with surrounding whitespace. The canonical
    split is decided by the shared allowlists, so the parsing is deterministic
    and offline.
    """
    text = (code or "").strip().upper()
    if not text:
        return None

    if "-" in text:
        parts = text.split("-")
        if len(parts) != 2:
            return None
        base, quote = parts
        if base in _BASE_ASSET_SET and quote in _QUOTE_ASSET_SET:
            return base, quote
        return None

    # Concatenated form: greedy longest-base first so ``BTCUSDT`` never splits
    # into ``BTCUSD`` + ``T``.
    for length in range(min(_MAX_BASE_LEN, len(text) - 1), 0, -1):
        base, quote = text[:length], text[length:]
        if base in _BASE_ASSET_SET and quote in _QUOTE_ASSET_SET:
            return base, quote
    return None


def is_crypto_symbol(code: str) -> bool:
    """Return True when ``code`` is an allowlisted crypto pair.

    Ambiguity rule (shared with :func:`normalize_crypto_symbol`): only
    ``allowlisted base + allowlisted quote`` counts as crypto, so a bare
    all-letter token stays a stock. ``AAPL``, ``SHOP``, ``ADA`` (base asset
    without a quote) and ``BTC`` (currency unit without a quote) are therefore
    **not** crypto — they keep the existing US ticker path.
    """
    return split_crypto_symbol(code) is not None


def normalize_crypto_symbol(code: str) -> Optional[str]:
    """Normalize a crypto pair to the canonical ``BASE-QUOTE`` uppercase form.

    Examples: ``btcusdt`` / ``BTCUSDT`` / ``btc-usdt`` -> ``BTC-USDT``.
    Returns ``None`` for anything that is not an allowlisted crypto pair.

    Ambiguity rule: a bare all-letter code of length <= 5 that is not a pair
    (e.g. ``AAPL``) is not crypto, so US tickers keep working exactly as
    before. A bare all-letter code that *is* claimed by the base allowlist but
    carries no quote (``ADA`` / ``BTC``) also stays a US ticker.
    """
    parts = split_crypto_symbol(code)
    if parts is None:
        return None
    return f"{parts[0]}-{parts[1]}"


def crypto_to_binance(code: str) -> Optional[str]:
    """Return the Binance trading symbol (``BTCUSDT``) or ``None``."""
    parts = split_crypto_symbol(code)
    if parts is None:
        return None
    return f"{parts[0]}{parts[1]}"


def crypto_to_yahoo(code: str) -> Optional[str]:
    """Return the Yahoo Finance crypto ticker (``BTC-USD``) or ``None``."""
    parts = split_crypto_symbol(code)
    if parts is None:
        return None
    base, quote = parts
    yahoo_quote = "USD" if quote in CRYPTO_STABLECOIN_QUOTES else quote
    return f"{base}-{yahoo_quote}"


def crypto_quote_currency(code: str) -> Optional[str]:
    """Return the quote currency label (``USD`` for stablecoin pairs)."""
    parts = split_crypto_symbol(code)
    if parts is None:
        return None
    base, quote = parts
    return "USD" if quote in CRYPTO_STABLECOIN_QUOTES else quote
