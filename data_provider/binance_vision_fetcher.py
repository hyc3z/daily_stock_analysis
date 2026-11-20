# -*- coding: utf-8 -*-
"""
===================================
BinanceVisionFetcher - 虚拟货币（加密货币）首选数据源 (Priority 1)
===================================

数据来源：Binance Vision 公开行情镜像（https://data-api.binance.vision）
特点：免密钥、响应快（~0.3s），只服务虚拟货币交易对（``BTC-USDT`` / ``BTCUSDT``）

能力：
1. 日线 K 线：``GET /api/v3/klines``（interval=1d，分页拉取，最大 1000 根/页）
2. 实时/近实时行情：``GET /api/v3/ticker/24hr``（24 小时滚动统计）

边界：
- 仅服务虚拟货币：非虚拟货币代码直接抛 ``DataSourceUnavailableError``，不发请求，
  也不会被 A 股/港股/美股路由选中（见 ``DataFetcherManager`` 的市场过滤）。
- 所有请求都带显式 timeout（``BINANCE_VISION_TIMEOUT_SEC``，默认 10s），不会挂死。
- 非 200 / 非 JSON / 结构异常统一抛 ``DataSourceUnavailableError``，交由 manager 降级。
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import pandas as pd
import requests

from .base import BaseFetcher, STANDARD_COLUMNS, DataSourceUnavailableError
from .realtime_types import RealtimeSource, UnifiedRealtimeQuote, safe_float, safe_int
from src.services.market_symbol_utils import (
    CRYPTO_MARKET,
    crypto_quote_currency,
    crypto_to_binance,
    normalize_crypto_symbol,
)

logger = logging.getLogger(__name__)

BINANCE_VISION_BASE_URL = "https://data-api.binance.vision"
_KLINES_PATH = "/api/v3/klines"
_TICKER_24H_PATH = "/api/v3/ticker/24hr"
_KLINE_LIMIT = 1000
# 单次分析最多翻 20 页（≈2 万根日线），确保异常情况下也不会无限循环。
_MAX_PAGES = 20
_DEFAULT_TIMEOUT_SEC = 10.0
_REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; DSA/1.0; +https://github.com/ZhuLinsen/daily_stock_analysis)",
    "Accept": "application/json",
}


def _env_float(name: str, default: float) -> float:
    """Read a positive float env var, falling back to ``default``."""
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except (TypeError, ValueError):
        logger.warning("[BinanceVision] 无法解析 %s=%r，使用默认值 %s", name, raw, default)
        return default
    return value if value > 0 else default


def _env_int(name: str, default: int) -> int:
    """Read an int env var, falling back to ``default``."""
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        logger.warning("[BinanceVision] 无法解析 %s=%r，使用默认值 %s", name, raw, default)
        return default


def _http_get_json(url: str, params: Optional[Dict[str, Any]], timeout: float) -> Any:
    """Fetch one JSON payload with an explicit timeout.

    独立成模块级函数，便于离线测试 monkeypatch HTTP 层（不发起真实网络请求）。
    """
    response = requests.get(url, params=params, timeout=timeout, headers=_REQUEST_HEADERS)
    if response.status_code != 200:
        raise DataSourceUnavailableError(
            f"[BinanceVision] HTTP {response.status_code} for {url}"
        )
    try:
        return response.json()
    except ValueError as exc:
        raise DataSourceUnavailableError(
            f"[BinanceVision] non-JSON payload from {url}: {exc}"
        ) from exc


class BinanceVisionFetcher(BaseFetcher):
    """Binance Vision 数据源：只服务虚拟货币交易对。"""

    name = "BinanceVisionFetcher"
    priority = _env_int("BINANCE_VISION_PRIORITY", 1)

    # 该 fetcher 只声明虚拟货币市场，manager 的市场路由据此过滤。
    supported_markets = frozenset({CRYPTO_MARKET})

    def __init__(self) -> None:
        self.timeout = _env_float("BINANCE_VISION_TIMEOUT_SEC", _DEFAULT_TIMEOUT_SEC)

    # ------------------------------------------------------------------
    # 能力声明
    # ------------------------------------------------------------------
    @classmethod
    def supports_market(cls, market: str) -> bool:
        """Return True only for the virtual-currency market."""
        return (market or "").strip().lower() in cls.supported_markets

    def is_available_for_request(self, capability: str = "") -> bool:
        """声明可服务的能力：日线与实时行情。非行情能力一律不可用。"""
        return (capability or "") in {"", "daily_data", "realtime_quote"}

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------
    @staticmethod
    def _canonical_symbol(stock_code: str) -> str:
        """Return the canonical ``BASE-QUOTE`` pair or raise a clear error."""
        canonical = normalize_crypto_symbol(stock_code)
        if canonical is None:
            raise DataSourceUnavailableError(
                f"[BinanceVision] {stock_code!r} 不是支持的虚拟货币交易对 "
                f"(示例: BTC-USDT / BTCUSDT)"
            )
        return canonical

    def _fetch_klines_page(self, symbol: str, start_ms: int, end_ms: int) -> List[list]:
        """Fetch one page of daily klines (max ``_KLINE_LIMIT`` rows)."""
        payload = _http_get_json(
            f"{BINANCE_VISION_BASE_URL}{_KLINES_PATH}",
            {
                "symbol": symbol,
                "interval": "1d",
                "limit": _KLINE_LIMIT,
                "startTime": start_ms,
                "endTime": end_ms,
            },
            self.timeout,
        )
        if not isinstance(payload, list):
            # Binance 错误响应形如 {"code": -1121, "msg": "Invalid symbol."}
            raise DataSourceUnavailableError(
                f"[BinanceVision] 非预期的 K 线返回结构 for {symbol}: {str(payload)[:200]}"
            )
        return payload

    # ------------------------------------------------------------------
    # 日线数据
    # ------------------------------------------------------------------
    def _fetch_raw_data(self, stock_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        """按日线分页拉取 ``/api/v3/klines`` 原始数据。"""
        canonical = self._canonical_symbol(stock_code)
        symbol = crypto_to_binance(canonical)
        if symbol is None:  # pragma: no cover - defensive, canonical is already validated
            raise DataSourceUnavailableError(f"[BinanceVision] 无法映射 {stock_code!r}")

        try:
            start_dt = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            end_dt = datetime.strptime(end_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except (TypeError, ValueError) as exc:
            raise DataSourceUnavailableError(
                f"[BinanceVision] 非法日期范围 {start_date!r} ~ {end_date!r}: {exc}"
            ) from exc

        start_ms = int(start_dt.timestamp() * 1000)
        # 包含 end_date 当天（次日 0 点前 1 毫秒）
        end_ms = int((end_dt + timedelta(days=1) - timedelta(milliseconds=1)).timestamp() * 1000)

        rows: List[list] = []
        cursor = start_ms
        for page in range(_MAX_PAGES):
            payload = self._fetch_klines_page(symbol, cursor, end_ms)
            if not payload:
                break
            rows.extend(payload)
            try:
                last_open_ms = int(payload[-1][0])
            except (IndexError, TypeError, ValueError) as exc:
                raise DataSourceUnavailableError(
                    f"[BinanceVision] K 线缺少 openTime for {symbol}: {exc}"
                ) from exc
            next_cursor = last_open_ms + 86_400_000
            if next_cursor <= cursor or next_cursor > end_ms:
                break
            cursor = next_cursor
            if len(payload) < _KLINE_LIMIT:
                break
            logger.debug(
                "[BinanceVision] %s 日线分页 %d/%d (rows=%d)", symbol, page + 1, _MAX_PAGES, len(rows)
            )
        else:
            logger.warning(
                "[BinanceVision] %s 日线达到分页上限 %d 页，返回已拉取的 %d 根",
                symbol,
                _MAX_PAGES,
                len(rows),
            )

        if not rows:
            logger.info("[BinanceVision] %s 在 %s ~ %s 无日线数据", symbol, start_date, end_date)
            return pd.DataFrame()

        normalized_rows: List[Dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, (list, tuple)) or len(row) < 8:
                raise DataSourceUnavailableError(
                    f"[BinanceVision] K 线结构异常 for {symbol}: {str(row)[:200]}"
                )
            normalized_rows.append(
                {
                    "open_time": row[0],
                    "open": row[1],
                    "high": row[2],
                    "low": row[3],
                    "close": row[4],
                    "volume": row[5],
                    "quote_volume": row[7],
                }
            )
        return pd.DataFrame(normalized_rows)

    def _normalize_data(self, df: pd.DataFrame, stock_code: str) -> pd.DataFrame:
        """把 klines 原始列标准化为 ``date/open/high/low/close/volume/amount/pct_chg``。"""
        if df is None or df.empty:
            return pd.DataFrame(columns=["code"] + STANDARD_COLUMNS)

        df = df.copy()
        df["date"] = pd.to_datetime(df["open_time"], unit="ms", utc=True).dt.date

        for column in ("open", "high", "low", "close", "volume", "quote_volume"):
            if column in df.columns:
                df[column] = pd.to_numeric(df[column], errors="coerce")

        df = df.sort_values("date")
        df = df.drop_duplicates(subset=["date"], keep="last").reset_index(drop=True)

        # 成交额口径：Binance 的 quoteAssetVolume（计价货币成交额）
        df["amount"] = df["quote_volume"] if "quote_volume" in df.columns else pd.NA

        df["pct_chg"] = df["close"].pct_change() * 100
        df["pct_chg"] = df["pct_chg"].fillna(0).round(2)
        df["volume"] = pd.to_numeric(df["volume"], errors="coerce")
        df["code"] = stock_code

        keep_cols = ["code"] + STANDARD_COLUMNS
        return df[[col for col in keep_cols if col in df.columns]]

    # ------------------------------------------------------------------
    # 实时行情
    # ------------------------------------------------------------------
    def get_realtime_quote(self, stock_code: str) -> Optional[UnifiedRealtimeQuote]:
        """用 ``/api/v3/ticker/24hr`` 构造统一实时行情。"""
        canonical = self._canonical_symbol(stock_code)
        symbol = crypto_to_binance(canonical)
        if symbol is None:  # pragma: no cover - defensive
            return None

        payload = _http_get_json(
            f"{BINANCE_VISION_BASE_URL}{_TICKER_24H_PATH}",
            {"symbol": symbol},
            self.timeout,
        )
        if not isinstance(payload, dict):
            raise DataSourceUnavailableError(
                f"[BinanceVision] 非预期的 24hr ticker 结构 for {symbol}: {str(payload)[:200]}"
            )

        price = safe_float(payload.get("lastPrice"))
        if price is None or price <= 0:
            raise DataSourceUnavailableError(
                f"[BinanceVision] 24hr ticker 缺少 lastPrice for {symbol}"
            )

        prev_close = safe_float(payload.get("prevClosePrice"))
        high = safe_float(payload.get("highPrice"))
        low = safe_float(payload.get("lowPrice"))
        open_price = safe_float(payload.get("openPrice"))
        change_amount = safe_float(payload.get("priceChange"))
        change_pct = safe_float(payload.get("priceChangePercent"))
        volume = safe_int(payload.get("volume"))
        amount = safe_float(payload.get("quoteVolume"))

        amplitude = None
        if high is not None and low is not None and prev_close and prev_close > 0:
            amplitude = round((high - low) / prev_close * 100, 2)

        provider_timestamp = None
        close_time_ms = safe_float(payload.get("closeTime"))
        if close_time_ms is not None and close_time_ms > 0:
            provider_timestamp = datetime.fromtimestamp(
                close_time_ms / 1000.0, tz=timezone.utc
            ).isoformat()

        missing_fields = [
            field
            for field, value in {
                "prev_close": prev_close,
                "volume": volume,
                "amount": amount,
            }.items()
            if value is None
        ]

        quote = UnifiedRealtimeQuote(
            code=canonical,
            name=canonical,
            source=RealtimeSource.FALLBACK,
            market=CRYPTO_MARKET,
            currency=crypto_quote_currency(canonical),
            data_quality="partial" if missing_fields else "ok",
            missing_fields=missing_fields or None,
            price=price,
            change_pct=round(change_pct, 2) if change_pct is not None else None,
            change_amount=round(change_amount, 4) if change_amount is not None else None,
            volume=volume,
            amount=amount,
            volume_ratio=None,
            turnover_rate=None,
            amplitude=amplitude,
            open_price=open_price,
            high=high,
            low=low,
            pre_close=prev_close,
            pe_ratio=None,
            pb_ratio=None,
            total_mv=None,
            circ_mv=None,
            provider_timestamp=provider_timestamp,
        )
        logger.info("[BinanceVision] 获取 %s 实时行情成功: 价格=%s", canonical, price)
        return quote


__all__ = ["BinanceVisionFetcher", "BINANCE_VISION_BASE_URL"]
