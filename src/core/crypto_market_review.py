# -*- coding: utf-8 -*-
"""24/7 virtual-currency (crypto) market-review data, prompt and fallback text.

The market-review pipeline (``src/core/market_review.py`` +
``src/market_analyzer.py``) is built around stock markets: index tables, market
breadth, limit-up/limit-down counts and sector rankings. Crypto has none of
those, so this module provides the crypto-specific slice:

* ``load_crypto_review_data`` — Binance Vision only: ``BTCUSDT`` / ``ETHUSDT``
  24h tickers (``/api/v3/ticker/24hr``) plus recent daily klines
  (``/api/v3/klines``) for context. There is no exchange calendar: crypto
  trades 24/7, and a market review is always meaningful.
* ``render_crypto_market_data_block`` / ``render_crypto_prompt_requirements`` —
  the LLM input blocks. They state explicitly that this is a 24/7 market, that
  limit-up/limit-down, Northbound and Dragon-Tiger data do not exist here, and
  that funding/macro/on-chain framing must never invent data.
* ``render_crypto_review_report`` — deterministic Markdown used when the LLM is
  unavailable, and the explicit "no data" section when Binance Vision is down.

Fail-open contract: nothing here raises. A provider outage yields
``CryptoReviewData(available=False, errors=[...])`` so the caller can emit an
explicit no-data review section instead of breaking the run.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)

# Binance Vision only (no exchange-calendar gating, no stock providers).
CRYPTO_REVIEW_PAIRS: tuple[str, ...] = ("BTC-USDT", "ETH-USDT")
CRYPTO_REVIEW_DISPLAY_NAMES: Dict[str, str] = {
    "BTC-USDT": "BTC",
    "ETH-USDT": "ETH",
}
CRYPTO_REVIEW_LOOKBACK_DAYS = 8
CRYPTO_REVIEW_KLINE_ROWS = 5
CRYPTO_REVIEW_DATA_SOURCE = "Binance Vision"

# Crypto review has no stock-only concepts at all. Kept as data so prompt,
# fallback report and tests read the same list.
CRYPTO_REVIEW_ABSENT_DATA = (
    ("limit", "无涨停/跌停（加密市场没有每日涨跌幅限制）", "no limit-up/limit-down (crypto has no daily price limit)"),
    ("northbound", "无北向资金/南向资金", "no Northbound/Southbound flows"),
    ("dragon_tiger", "无龙虎榜、无融资融券", "no Dragon-Tiger list and no margin financing"),
    ("breadth", "无涨跌家数、无板块/概念涨跌榜", "no advance/decline breadth and no sector/theme rankings"),
)


@dataclass
class CryptoReviewData:
    """Crypto market review inputs. ``available`` is False when nothing loaded."""

    quotes: List[Dict[str, Any]] = field(default_factory=list)
    klines: Dict[str, List[Dict[str, Any]]] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)
    as_of: str = ""

    @property
    def available(self) -> bool:
        return bool(self.quotes)

    def to_context(self) -> Dict[str, Any]:
        """Projection consumed by the market-review prompt and payload."""
        return {
            "available": self.available,
            "source": CRYPTO_REVIEW_DATA_SOURCE,
            "as_of": self.as_of,
            "quotes": list(self.quotes),
            "klines": {pair: list(rows) for pair, rows in self.klines.items()},
            "errors": list(self.errors),
        }


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def crypto_review_data_from_context(context: Optional[Dict[str, Any]]) -> CryptoReviewData:
    """Rebuild ``CryptoReviewData`` from a ``MarketOverview.crypto_context`` dict.

    Keeps the analyzer/prompt layers free of dict-shape knowledge: the context
    projection written by ``to_context`` round-trips through this constructor.
    """

    if not isinstance(context, dict):
        return CryptoReviewData()
    quotes = [item for item in context.get("quotes") or [] if isinstance(item, dict)]
    raw_klines = context.get("klines")
    klines: Dict[str, List[Dict[str, Any]]] = {}
    if isinstance(raw_klines, dict):
        for pair, rows in raw_klines.items():
            if isinstance(rows, list):
                klines[str(pair)] = [row for row in rows if isinstance(row, dict)]
    errors = [str(error) for error in context.get("errors") or []]
    return CryptoReviewData(
        quotes=quotes,
        klines=klines,
        errors=errors,
        as_of=str(context.get("as_of") or ""),
    )


def _safe_float(value: Any) -> Optional[float]:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None  # drop NaN


def _quote_to_index_item(pair: str, quote: Any) -> Dict[str, Any]:
    """``UnifiedRealtimeQuote`` → the dict shape ``MarketAnalyzer`` expects."""
    price = _safe_float(getattr(quote, "price", None))
    change_pct = _safe_float(getattr(quote, "change_pct", None))
    change_amount = _safe_float(getattr(quote, "change_amount", None))
    return {
        "code": pair,
        "name": CRYPTO_REVIEW_DISPLAY_NAMES.get(pair, pair),
        "current": price if price is not None else 0.0,
        "change": change_amount if change_amount is not None else 0.0,
        "change_pct": change_pct if change_pct is not None else 0.0,
        "open": _safe_float(getattr(quote, "open_price", None)) or 0.0,
        "high": _safe_float(getattr(quote, "high", None)) or 0.0,
        "low": _safe_float(getattr(quote, "low", None)) or 0.0,
        "prev_close": _safe_float(getattr(quote, "pre_close", None)) or 0.0,
        "volume": _safe_float(getattr(quote, "volume", None)) or 0.0,
        "amount": _safe_float(getattr(quote, "amount", None)) or 0.0,
        "amplitude": _safe_float(getattr(quote, "amplitude", None)) or 0.0,
    }


def _summarize_klines(df: Any, *, limit: int = CRYPTO_REVIEW_KLINE_ROWS) -> List[Dict[str, Any]]:
    """Tail rows of the normalized daily frame → plain dicts (no NaN)."""
    if df is None or getattr(df, "empty", True):
        return []
    rows: List[Dict[str, Any]] = []
    tail = df.tail(limit)
    for _, row in tail.iterrows():
        rows.append(
            {
                "date": str(row.get("date") or ""),
                "close": _safe_float(row.get("close")),
                "pct_chg": _safe_float(row.get("pct_chg")),
                "amount": _safe_float(row.get("amount")),
            }
        )
    return rows


def load_crypto_review_data(fetcher: Optional[Any] = None) -> CryptoReviewData:
    """Load BTC/ETH 24h tickers + daily klines from Binance Vision (fail-open).

    ``fetcher`` is injectable for offline tests; when omitted the Binance
    Vision fetcher is constructed lazily. Every provider call is wrapped, so an
    outage degrades to ``available=False`` + ``errors`` instead of raising.
    """

    data = CryptoReviewData(as_of=_utc_now_iso())
    if fetcher is None:
        try:
            from data_provider.binance_vision_fetcher import BinanceVisionFetcher

            fetcher = BinanceVisionFetcher()
        except Exception as exc:  # pragma: no cover - import/config failure
            data.errors.append(f"Binance Vision fetcher unavailable: {exc}")
            logger.warning("[CryptoReview] component=crypto_review status=fetcher_unavailable error=%s", exc)
            return data

    for pair in CRYPTO_REVIEW_PAIRS:
        try:
            quote = fetcher.get_realtime_quote(pair)
        except Exception as exc:
            quote = None
            data.errors.append(f"{pair} 24h ticker failed: {exc}")
            logger.warning(
                "[CryptoReview] component=crypto_review action=ticker status=failed pair=%s error=%s",
                pair,
                exc,
            )
        if quote is not None:
            data.quotes.append(_quote_to_index_item(pair, quote))

        try:
            df = fetcher.get_daily_data(pair, days=CRYPTO_REVIEW_LOOKBACK_DAYS)
        except Exception as exc:
            df = None
            data.errors.append(f"{pair} daily klines failed: {exc}")
            logger.warning(
                "[CryptoReview] component=crypto_review action=klines status=failed pair=%s error=%s",
                pair,
                exc,
            )
        rows = _summarize_klines(df)
        if rows:
            data.klines[pair] = rows

    logger.info(
        "[CryptoReview] component=crypto_review action=load status=%s quotes=%d errors=%d",
        "success" if data.available else "empty",
        len(data.quotes),
        len(data.errors),
    )
    return data


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------
def _turnover_billion(amount: Optional[float]) -> str:
    if not amount:
        return "-"
    return f"{amount / 1e9:.2f}"


def _pct(value: Optional[float]) -> str:
    if value is None:
        return "-"
    return f"{value:+.2f}%"


def _price(value: Optional[float]) -> str:
    if value is None:
        return "-"
    return f"{value:,.2f}"


def _absent_data_lines(language: str) -> List[str]:
    index = 2 if language == "en" else 1
    return [f"- {row[index]}" for row in CRYPTO_REVIEW_ABSENT_DATA]


def render_crypto_prompt_requirements(language: str) -> str:
    """Explicit market-boundary requirements for the crypto review prompt."""
    if language == "en":
        return """## Crypto Market Rules (must follow)
- This is a 24/7 continuously traded market: there is no opening/closing bell, no daily price limit, and no T+1 settlement. Do not describe a "close" or "next session open".
- Data below comes from Binance Vision only (BTCUSDT / ETHUSDT 24h tickers plus daily klines). Do not invent index levels, market breadth, turnover aggregates, fund flows, funding rates, open interest, liquidation or on-chain numbers that are not provided.
- Never apply China A-share concepts: no limit-up/limit-down boards, no Northbound/Southbound flows, no Dragon-Tiger list, no margin financing, no A-share sector rotation.
- You may frame the discussion with funding-rate/macro/on-chain arguments *as reasoning*, but never state a specific funding rate, open-interest, liquidation or on-chain value unless it appears in the data above. Say "not provided" when the data is missing.
- Keep the crypto-vs-stock distinction explicit: no P/E, no market-cap or earnings framing for a crypto asset."""
    return """## 虚拟货币市场规则（必须遵守）
- 这是 **24/7 连续交易**市场：没有开盘/收盘、没有每日涨跌幅限制、不适用 T+1。不要描述“收盘”“次日开盘”。
- 下方数据只来自 Binance Vision（BTCUSDT / ETHUSDT 24h ticker + 日线）。未提供的数据一律不得编造：指数点位、涨跌家数、成交额汇总、资金流、资金费率、未平仓合约、强平、链上指标。
- 禁止套用 A 股概念：不要使用涨停/跌停、北向资金、龙虎榜、融资融券、A 股板块轮动。
- 可以用资金费率/宏观/链上逻辑做 **推理框架**，但除非数据区给出了具体数值，否则不得给出具体资金费率、未平仓、强平或链上数值；缺失时写“未提供数据”。
- 明确加密资产与股票的区别：不要讨论市盈率、市值、财报等股票框架。"""


def render_crypto_market_data_block(data: CryptoReviewData, language: str) -> str:
    """Prompt-side crypto market data block (24h tickers + daily klines)."""
    if language == "en":
        lines = ["### Crypto Market Data (24/7, Binance Vision)"]
        if data.as_of:
            lines.append(f"- Snapshot (UTC): {data.as_of}")
        if not data.available:
            lines.append("- No market data available (Binance Vision unavailable).")
            lines.extend(_absent_data_lines(language))
            return "\n".join(lines)
        for quote in data.quotes:
            lines.append(
                f"- {quote['name']} ({quote['code']}): last {_price(quote['current'])} USDT, "
                f"24h change {_pct(quote['change_pct'])}, 24h open {_price(quote['open'])}, "
                f"24h high {_price(quote['high'])}, 24h low {_price(quote['low'])}, "
                f"24h quote volume {_turnover_billion(quote['amount'])} (bn USDT)"
            )
        missing = [
            CRYPTO_REVIEW_DISPLAY_NAMES.get(pair, pair)
            for pair in CRYPTO_REVIEW_PAIRS
            if pair not in {quote["code"] for quote in data.quotes}
        ]
        if missing:
            lines.append(f"- Data unavailable for: {', '.join(missing)}")
        for pair, rows in data.klines.items():
            if not rows:
                continue
            lines.append("")
            lines.append(f"#### {CRYPTO_REVIEW_DISPLAY_NAMES.get(pair, pair)} recent daily klines (UTC)")
            lines.append("| Date | Close | Change | Quote volume (bn USDT) |")
            lines.append("|------|-------|--------|------------------------|")
            for row in rows:
                lines.append(
                    f"| {row['date']} | {_price(row['close'])} | {_pct(row['pct_chg'])} | "
                    f"{_turnover_billion(row['amount'])} |"
                )
        if data.errors:
            lines.append("")
            lines.append("- Partial data errors (do not fill the gaps with invented numbers):")
            lines.extend(f"  - {error}" for error in data.errors)
        lines.append("")
        lines.append("### Not available for this market")
        lines.extend(_absent_data_lines(language))
        return "\n".join(lines)

    lines = ["### 虚拟货币市场数据（24/7，Binance Vision）"]
    if data.as_of:
        lines.append(f"- 数据时间（UTC）：{data.as_of}")
    if not data.available:
        lines.append("- 暂无行情数据（Binance Vision 不可用）。")
        lines.extend(_absent_data_lines(language))
        return "\n".join(lines)
    for quote in data.quotes:
        lines.append(
            f"- {quote['name']}（{quote['code']}）：最新 {_price(quote['current'])} USDT，"
            f"24h 涨跌 {_pct(quote['change_pct'])}，24h 开 {_price(quote['open'])}，"
            f"24h 高 {_price(quote['high'])}，24h 低 {_price(quote['low'])}，"
            f"24h 计价成交额 {_turnover_billion(quote['amount'])}（十亿 USDT）"
        )
    missing = [
        CRYPTO_REVIEW_DISPLAY_NAMES.get(pair, pair)
        for pair in CRYPTO_REVIEW_PAIRS
        if pair not in {quote["code"] for quote in data.quotes}
    ]
    if missing:
        lines.append(f"- 以下标的数据缺失：{', '.join(missing)}")
    for pair, rows in data.klines.items():
        if not rows:
            continue
        lines.append("")
        lines.append(f"#### {CRYPTO_REVIEW_DISPLAY_NAMES.get(pair, pair)} 近 {len(rows)} 个 UTC 日线")
        lines.append("| 日期 | 收盘 | 涨跌幅 | 计价成交额（十亿 USDT） |")
        lines.append("|------|------|--------|--------------------------|")
        for row in rows:
            lines.append(
                f"| {row['date']} | {_price(row['close'])} | {_pct(row['pct_chg'])} | "
                f"{_turnover_billion(row['amount'])} |"
            )
    if data.errors:
        lines.append("")
        lines.append("- 部分数据错误（不得用编造数值填补）：")
        lines.extend(f"  - {error}" for error in data.errors)
    lines.append("")
    lines.append("### 该市场没有的数据")
    lines.extend(_absent_data_lines(language))
    return "\n".join(lines)


def render_crypto_review_report(
    data: CryptoReviewData,
    *,
    date: str,
    language: str,
) -> str:
    """Deterministic crypto recap used without an LLM and for the no-data case."""
    title = (
        f"## {date} Virtual-Currency Market Recap"
        if language == "en"
        else f"## {date} 虚拟货币大盘复盘"
    )
    if not data.available:
        return _render_crypto_no_data_body(title=title, language=language, errors=data.errors)

    lines: List[str] = [title, ""]
    if language == "en":
        lines.append("### 1. Market Summary")
        lines.append(
            f"24/7 crypto snapshot from {CRYPTO_REVIEW_DATA_SOURCE} (UTC: {data.as_of or '-'}). "
            "Crypto has no closing bell, no daily price limit and no T+1 settlement."
        )
        lines.append("")
        lines.append("### 2. Major Pairs (24h)")
        lines.append("| Pair | Last (USDT) | 24h Change | 24h High | 24h Low | 24h Quote Volume (bn USDT) |")
        lines.append("|------|-------------|-----------|----------|---------|---------------------------|")
        for quote in data.quotes:
            lines.append(
                f"| {quote['name']} ({quote['code']}) | {_price(quote['current'])} | "
                f"{_pct(quote['change_pct'])} | {_price(quote['high'])} | {_price(quote['low'])} | "
                f"{_turnover_billion(quote['amount'])} |"
            )
        for pair, rows in data.klines.items():
            if not rows:
                continue
            lines.append("")
            lines.append(f"### Daily Klines: {CRYPTO_REVIEW_DISPLAY_NAMES.get(pair, pair)}")
            lines.append("| Date | Close | Change | Quote volume (bn USDT) |")
            lines.append("|------|-------|--------|------------------------|")
            for row in rows:
                lines.append(
                    f"| {row['date']} | {_price(row['close'])} | {_pct(row['pct_chg'])} | "
                    f"{_turnover_billion(row['amount'])} |"
                )
        lines.append("")
        lines.append("### 3. Data Limits")
        lines.extend(_absent_data_lines(language))
        if data.errors:
            lines.extend(f"- {error}" for error in data.errors)
        lines.append("")
        lines.append("### 4. Risk Alerts")
        lines.append(
            "Crypto is a 24/7 high-volatility market. The data above is for reference only and is "
            "not investment advice; funding/macro/on-chain figures are not provided by this source."
        )
        return "\n".join(lines)

    lines.append("### 一、盘面总览")
    lines.append(
        f"数据来自 {CRYPTO_REVIEW_DATA_SOURCE} 的 24/7 快照（UTC：{data.as_of or '-'}）。"
        "加密市场没有收盘、没有每日涨跌幅限制、不适用 T+1。"
    )
    lines.append("")
    lines.append("### 二、主要交易对（24 小时）")
    lines.append("| 交易对 | 最新（USDT） | 24h 涨跌 | 24h 最高 | 24h 最低 | 24h 计价成交额（十亿 USDT） |")
    lines.append("|--------|--------------|----------|----------|----------|----------------------------|")
    for quote in data.quotes:
        lines.append(
            f"| {quote['name']}（{quote['code']}） | {_price(quote['current'])} | "
            f"{_pct(quote['change_pct'])} | {_price(quote['high'])} | {_price(quote['low'])} | "
            f"{_turnover_billion(quote['amount'])} |"
        )
    for pair, rows in data.klines.items():
        if not rows:
            continue
        lines.append("")
        lines.append(f"### 日线：{CRYPTO_REVIEW_DISPLAY_NAMES.get(pair, pair)}")
        lines.append("| 日期 | 收盘 | 涨跌幅 | 计价成交额（十亿 USDT） |")
        lines.append("|------|------|--------|--------------------------|")
        for row in rows:
            lines.append(
                f"| {row['date']} | {_price(row['close'])} | {_pct(row['pct_chg'])} | "
                f"{_turnover_billion(row['amount'])} |"
            )
    lines.append("")
    lines.append("### 三、数据边界")
    lines.extend(_absent_data_lines(language))
    if data.errors:
        lines.extend(f"- {error}" for error in data.errors)
    lines.append("")
    lines.append("### 四、风险提示")
    lines.append(
        "加密市场 24/7 连续交易、波动大；以上数据仅供参考，不构成投资建议。"
        "资金费率、未平仓、强平与链上指标本数据源不提供。"
    )
    return "\n".join(lines)


def _render_crypto_no_data_body(title: str, *, language: str, errors: Iterable[str] = ()) -> str:
    if language == "en":
        lines = [
            title,
            "",
            "### 1. Market Summary",
            "No data available: the Binance Vision source (BTCUSDT / ETHUSDT) could not be reached, "
            "so this crypto recap is intentionally data-free.",
            "",
            "### 2. Data Limits",
            "- No prices, changes, volumes or klines were fetched; no values are estimated or invented.",
            "- This is a 24/7 market: no opening/closing session, no limit-up/limit-down, no T+1.",
            "- No market breadth, Northbound flows, Dragon-Tiger list, margin financing or sector rankings.",
            "",
            "### 3. Risk Alerts",
            "Retry once Binance Vision is reachable. The other market regions in this run are unaffected. "
            "Nothing here is investment advice.",
        ]
    else:
        lines = [
            title,
            "",
            "### 一、盘面总览",
            "暂无数据：Binance Vision（BTCUSDT / ETHUSDT）本次不可用，因此本段虚拟货币复盘不包含任何行情数值。",
            "",
            "### 二、数据边界",
            "- 未获取到价格、涨跌幅、成交额与日线；不对任何数值做估算或编造。",
            "- 该市场 24/7 连续交易：没有开盘/收盘、没有涨跌停、不适用 T+1。",
            "- 没有涨跌家数、北向资金、龙虎榜、融资融券与板块涨跌榜。",
            "",
            "### 三、风险提示",
            "待 Binance Vision 恢复后重试；本轮其它市场区域不受影响。以上内容不构成投资建议。",
        ]
    error_list = [str(error).strip() for error in errors if str(error).strip()]
    if error_list:
        lines.append("")
        lines.append("### Data Errors" if language == "en" else "### 数据错误")
        lines.extend(f"- {error}" for error in error_list)
    return "\n".join(lines)


def render_crypto_no_data_report(*, date: str, language: str, errors: Iterable[str] = ()) -> str:
    """Explicit no-data crypto recap section (used when the region crashes)."""
    title = (
        f"## {date} Virtual-Currency Market Recap"
        if language == "en"
        else f"## {date} 虚拟货币大盘复盘"
    )
    return _render_crypto_no_data_body(title, language=language, errors=errors)


__all__ = [
    "CRYPTO_REVIEW_ABSENT_DATA",
    "CRYPTO_REVIEW_DATA_SOURCE",
    "CRYPTO_REVIEW_DISPLAY_NAMES",
    "CRYPTO_REVIEW_PAIRS",
    "CryptoReviewData",
    "crypto_review_data_from_context",
    "load_crypto_review_data",
    "render_crypto_market_data_block",
    "render_crypto_no_data_report",
    "render_crypto_prompt_requirements",
    "render_crypto_review_report",
]
