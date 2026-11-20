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

import re
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


# ---------------------------------------------------------------------------
# Chat-side crypto resolution: free-text pair spans + a small alias map.
# ---------------------------------------------------------------------------
# Chat input is prose, not a symbol field: users type "分析BTCUSDT",
# "看看比特币走势" or "BTC 怎么样". These helpers let the intent layer and the
# chat stock-scope layer claim those mentions *before* generic code/ticker
# regexes shred them (``BTC-USDT`` used to become the two bogus US tickers
# ``BTC`` + ``USDT``). They stay offline and deterministic: alias lookup plus
# the same allowlists above, no provider calls.
CRYPTO_DEFAULT_QUOTE = "USDT"

# Intentionally tiny, human-maintained name -> base-asset map for Chinese chat
# input. Add an entry only when users actually type the name; never generate it
# mechanically from the base allowlist (unlisted names must stay unresolved
# rather than resolve to an invented pair).
CRYPTO_NAME_ALIASES: dict[str, str] = {
    "比特币": "BTC",
    "以太坊": "ETH",
    "以太币": "ETH",
    "狗狗币": "DOGE",
    "索拉纳": "SOL",
}

_CRYPTO_ALIAS_RE = re.compile(
    "|".join(
        re.escape(name)
        for name in sorted(CRYPTO_NAME_ALIASES, key=len, reverse=True)
    )
)
# ASCII run that may carry a pair: ``BTCUSDT`` / ``BTC-USDT`` / ``eth-btc``.
# Digit-leading runs are excluded on purpose: numeric stock codes are handled by
# the existing code patterns and never form a crypto pair.
_CRYPTO_RUN_RE = re.compile(
    r"(?<![A-Za-z0-9])[A-Za-z][A-Za-z0-9]*(?:-[A-Za-z][A-Za-z0-9]*)*(?![A-Za-z0-9])"
)


def crypto_pair_from_base(base: str) -> Optional[str]:
    """Return ``BASE-USDT`` for an allowlisted base asset, else ``None``."""

    normalized = (base or "").strip().upper()
    if normalized in _BASE_ASSET_SET:
        return f"{normalized}-{CRYPTO_DEFAULT_QUOTE}"
    return None


def crypto_alias_pair(text: str) -> Optional[str]:
    """Return the default pair of a documented Chinese alias (``比特币`` -> ``BTC-USDT``)."""

    base = CRYPTO_NAME_ALIASES.get((text or "").strip())
    return crypto_pair_from_base(base) if base else None


def iter_crypto_pair_spans(text: str) -> list[tuple[int, int, str]]:
    """Yield ``(start, end, canonical_pair)`` for allowlisted pairs inside ``text``."""

    spans: list[tuple[int, int, str]] = []
    for match in _CRYPTO_RUN_RE.finditer(text or ""):
        pair = normalize_crypto_symbol(match.group())
        if pair is not None:
            spans.append((match.start(), match.end(), pair))
    return spans


def iter_crypto_alias_spans(text: str) -> list[tuple[int, int, str]]:
    """Yield ``(start, end, canonical_pair)`` for documented Chinese aliases."""

    spans: list[tuple[int, int, str]] = []
    for match in _CRYPTO_ALIAS_RE.finditer(text or ""):
        pair = crypto_alias_pair(match.group())
        if pair is not None:
            spans.append((match.start(), match.end(), pair))
    return spans


def iter_crypto_chat_spans(text: str) -> list[tuple[int, int, str]]:
    """Yield every crypto mention span in chat text, ordered by position.

    Combines pair-shaped runs and documented aliases. Overlaps are impossible by
    construction (aliases are CJK, pair runs are ASCII), but the result is
    sorted so callers can merge it with other span sources directly.
    """

    spans = iter_crypto_pair_spans(text) + iter_crypto_alias_spans(text)
    spans.sort(key=lambda span: (span[0], span[1]))
    return spans


def _is_listed_stock_ticker(code: str) -> bool:
    """True when a bare uppercase token is a real listed US ticker.

    Imported lazily so this dependency-light module never forms an import cycle
    with the resolver/stock-DB modules.
    """

    try:
        from src.services.name_to_code_resolver import US_stock_code_match
    except Exception:  # pragma: no cover - defensive, stock DB is always importable
        return False
    return bool(US_stock_code_match(code))


def resolve_crypto_chat_symbol(
    text: str,
    *,
    allow_bare_base: bool = False,
) -> Optional[str]:
    """Resolve one chat token/argument to the canonical ``BASE-QUOTE`` pair.

    Resolution order: pair forms (``BTC-USDT`` / ``BTCUSDT``), documented
    Chinese aliases, and — only when ``allow_bare_base`` is set — a bare
    allowlisted base asset (``BTC`` -> ``BTC-USDT``). The bare-base branch is
    gated on the local stock DB: a token that resolves to a listed ticker keeps
    the existing stock path, so stock parsing is never weakened.
    """

    normalized = (text or "").strip()
    if not normalized:
        return None

    pair = normalize_crypto_symbol(normalized)
    if pair is not None:
        return pair

    alias_pair = crypto_alias_pair(normalized)
    if alias_pair is not None:
        return alias_pair

    if allow_bare_base:
        base = normalized.upper()
        if base in _BASE_ASSET_SET and not _is_listed_stock_ticker(base):
            return f"{base}-{CRYPTO_DEFAULT_QUOTE}"
    return None
