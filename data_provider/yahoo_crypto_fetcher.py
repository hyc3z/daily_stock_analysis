# -*- coding: utf-8 -*-
"""
===================================
YahooCryptoFetcher - 虚拟货币 Yahoo Finance 兜底数据源 (Priority 3)
===================================

数据来源：Yahoo Finance（通过 yfinance 库，符号形如 ``BTC-USD``）
定位：Binance Vision 不可用时的虚拟货币兜底；复用 ``YfinanceFetcher`` 的
      日线/实时行情实现，只替换符号映射与错误语义。

关键约定：
1. 只服务虚拟货币交易对：``BTC-USDT`` / ``BTCUSDT`` 等统一映射到
   ``BTC-USD`` 形态（稳定币计价统一折算为 USD 报价）。
2. 所有失败都降级为 ``DataSourceUnavailableError``；其中 Yahoo 限流
   （``YFRateLimitError`` / HTTP 429）会给出明确提示，绝不拖垮整条分析链路。
3. 优先级来自 ``YAHOO_CRYPTO_PRIORITY``（默认 3）。
"""

from __future__ import annotations

import logging
import os
from typing import Optional

import pandas as pd

from .base import DataSourceUnavailableError
from .realtime_types import UnifiedRealtimeQuote
from .yfinance_fetcher import YfinanceFetcher
from src.services.market_symbol_utils import (
    CRYPTO_MARKET,
    crypto_quote_currency,
    crypto_to_yahoo,
    is_crypto_symbol,
    normalize_crypto_symbol,
)

logger = logging.getLogger(__name__)

# Yahoo 限流的可辨识特征：yfinance 抛 YFRateLimitError("Too Many Requests. Rate limited...")
# 或底层 HTTP 429。按消息/异常链一起匹配，避免依赖单一 yfinance 版本。
_RATE_LIMIT_MARKERS = (
    "rate limit",
    "rate-limit",
    "ratelimit",
    "too many requests",
    "429",
)


def _iter_exception_chain(exc: BaseException):
    """Yield ``exc`` and its chained causes without looping on cycles."""
    current: Optional[BaseException] = exc
    visited: set[int] = set()
    while current is not None and id(current) not in visited:
        visited.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


def is_yahoo_rate_limit_error(exc: BaseException) -> bool:
    """Return True when ``exc`` (or its chain) looks like a Yahoo rate limit."""
    for item in _iter_exception_chain(exc):
        if "ratelimit" in type(item).__name__.lower():
            return True
        text = str(item).lower()
        if any(marker in text for marker in _RATE_LIMIT_MARKERS):
            return True
    return False


def _env_int(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        logger.warning("[YahooCrypto] 无法解析 %s=%r，使用默认值 %s", name, raw, default)
        return default


class YahooCryptoFetcher(YfinanceFetcher):
    """虚拟货币 Yahoo Finance 兜底数据源（复用 YfinanceFetcher 实现）。"""

    name = "YahooCryptoFetcher"
    priority = _env_int("YAHOO_CRYPTO_PRIORITY", 3)

    # 该 fetcher 只声明虚拟货币市场，manager 的市场路由据此过滤。
    supported_markets = frozenset({CRYPTO_MARKET})

    @classmethod
    def supports_market(cls, market: str) -> bool:
        """Return True only for the virtual-currency market."""
        return (market or "").strip().lower() in cls.supported_markets

    def is_available_for_request(self, capability: str = "") -> bool:
        """声明可服务的能力：日线与实时行情。非行情能力一律不可用。"""
        return (capability or "") in {"", "daily_data", "realtime_quote"}

    # ------------------------------------------------------------------
    # 符号映射
    # ------------------------------------------------------------------
    def _convert_stock_code(self, stock_code: str) -> str:
        """Map any supported crypto pair to the Yahoo symbol (``BTC-USD``)."""
        yahoo_symbol = crypto_to_yahoo(stock_code)
        if yahoo_symbol is None:
            raise DataSourceUnavailableError(
                f"[YahooCrypto] {stock_code!r} 不是支持的虚拟货币交易对 "
                f"(示例: BTC-USDT / BTCUSDT)"
            )
        return yahoo_symbol

    def _supports_realtime_quote(self, stock_code: str) -> bool:
        """Crypto pairs are served here; keep the parent's stock families too."""
        return is_crypto_symbol(stock_code) or super()._supports_realtime_quote(stock_code)

    @staticmethod
    def _rate_limit_error(stock_code: str, exc: BaseException) -> DataSourceUnavailableError:
        return DataSourceUnavailableError(
            f"[YahooCrypto] {stock_code}: Yahoo Finance 限流（HTTP 429 / YFRateLimitError），"
            f"已降级到其它虚拟货币数据源: {exc}"
        )

    # ------------------------------------------------------------------
    # 日线数据（复用父类实现，只替换符号与错误语义）
    # ------------------------------------------------------------------
    def _fetch_raw_data(self, stock_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        yahoo_symbol = crypto_to_yahoo(stock_code)
        if yahoo_symbol is None:
            raise DataSourceUnavailableError(
                f"[YahooCrypto] {stock_code!r} 不是支持的虚拟货币交易对"
            )
        try:
            return super()._fetch_raw_data(yahoo_symbol, start_date, end_date)
        except Exception as exc:  # noqa: BLE001 - normalize provider failures
            if is_yahoo_rate_limit_error(exc):
                raise self._rate_limit_error(stock_code, exc) from exc
            raise

    # ------------------------------------------------------------------
    # 实时行情（复用父类实现，补齐虚拟货币市场标签）
    # ------------------------------------------------------------------
    def get_realtime_quote(self, stock_code: str) -> Optional[UnifiedRealtimeQuote]:
        canonical = normalize_crypto_symbol(stock_code)
        if canonical is None:
            return None
        yahoo_symbol = crypto_to_yahoo(canonical)
        if yahoo_symbol is None:  # pragma: no cover - defensive
            return None

        try:
            quote = super().get_realtime_quote(yahoo_symbol)
        except Exception as exc:  # noqa: BLE001 - normalize provider failures
            if is_yahoo_rate_limit_error(exc):
                raise self._rate_limit_error(stock_code, exc) from exc
            raise DataSourceUnavailableError(
                f"[YahooCrypto] {canonical}: Yahoo Finance 获取实时行情失败: {exc}"
            ) from exc

        if quote is None:
            # 父类对限流错误内部已降级为 None，这里给出明确的原因提示，
            # 由 manager 记录并继续降级（不抛未捕获异常）。
            raise DataSourceUnavailableError(
                f"[YahooCrypto] {canonical}: Yahoo Finance 未返回可用实时行情"
                f"（当前环境常见原因：HTTP 429 限流）"
            )

        # 父类按美股语义填充了 code/market/currency，这里收敛回虚拟货币口径。
        quote.code = canonical
        quote.name = quote.name or canonical
        quote.market = CRYPTO_MARKET
        quote.currency = crypto_quote_currency(canonical)
        logger.info("[YahooCrypto] 获取 %s 实时行情成功: 价格=%s", canonical, quote.price)
        return quote


__all__ = ["YahooCryptoFetcher", "is_yahoo_rate_limit_error"]
