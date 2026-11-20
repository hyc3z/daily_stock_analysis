# -*- coding: utf-8 -*-
"""Shared normalization rules for market-review region values."""

from typing import Optional


# Stock markets: canonical order, and the exact set expanded by ``both``.
# ``both`` intentionally keeps its documented six-stock-market meaning so an
# existing ``MARKET_REVIEW_REGION=both`` deployment does not start running a
# crypto review (and its extra provider calls) after an upgrade.
MARKET_REVIEW_REGION_ORDER = ("cn", "hk", "us", "jp", "kr", "tw")
# ``crypto`` is a 24/7 region: it must be requested explicitly (``crypto`` or a
# comma subset such as ``cn,crypto``) and is always appended last so the
# canonical order stays stable for the stock markets.
CRYPTO_MARKET_REVIEW_REGION = "crypto"
MARKET_REVIEW_REGION_ORDER_WITH_CRYPTO = (*MARKET_REVIEW_REGION_ORDER, CRYPTO_MARKET_REVIEW_REGION)
MARKET_REVIEW_REGION_SET = frozenset(MARKET_REVIEW_REGION_ORDER_WITH_CRYPTO)
MARKET_REVIEW_REGION_ALL = ",".join(MARKET_REVIEW_REGION_ORDER)
MARKET_REVIEW_REGION_VALID_INPUTS = (*MARKET_REVIEW_REGION_ORDER_WITH_CRYPTO, "both")


def normalize_market_review_region_lenient(value: Optional[str]) -> Optional[str]:
    """Normalize persistent config input while preserving legacy filtering.

    ``None`` and an empty string retain the historical ``cn`` default. Comma
    lists keep only supported markets (``crypto`` included, ordered last), and
    ``both`` expands to the six stock markets. ``None`` is returned only when a
    non-defaultable value has no valid token.
    """

    normalized = str(value or "cn").strip().lower()
    if normalized in MARKET_REVIEW_REGION_SET:
        return normalized
    if normalized == "both":
        return MARKET_REVIEW_REGION_ALL

    if "," in normalized:
        requested = {token.strip() for token in normalized.split(",") if token.strip()}
        if "both" in requested:
            return MARKET_REVIEW_REGION_ALL
        regions = [region for region in MARKET_REVIEW_REGION_ORDER_WITH_CRYPTO if region in requested]
        if regions:
            return ",".join(regions)

    return None


def normalize_market_review_region_strict(value: str) -> str:
    """Validate and canonicalize a request-scoped market-review region.

    Unlike persistent configuration parsing, request input is fail-fast: empty
    values, empty tokens, unknown tokens, and mixing ``both`` with other tokens
    raise ``ValueError`` instead of being filtered or defaulted.
    """

    normalized = value.strip().lower()
    valid_hint = (
        f"{', '.join(MARKET_REVIEW_REGION_VALID_INPUTS)}，"
        "或 cn/hk/us/jp/kr/tw/crypto 的合法逗号分隔组合"
    )
    if not normalized:
        raise ValueError(f"region 不能为空；合法值：{valid_hint}")

    tokens = [token.strip() for token in normalized.split(",")]
    if any(not token for token in tokens):
        raise ValueError(f"region 不能包含空项；合法值：{valid_hint}")

    invalid_tokens = sorted({token for token in tokens if token not in MARKET_REVIEW_REGION_SET and token != "both"})
    if invalid_tokens:
        raise ValueError(
            f"region 包含非法值：{', '.join(invalid_tokens)}；合法值：{valid_hint}"
        )

    if "both" in tokens:
        if len(tokens) != 1:
            raise ValueError("region 中 both 必须单独使用，不能与其他市场混合")
        return MARKET_REVIEW_REGION_ALL

    requested = set(tokens)
    return ",".join(region for region in MARKET_REVIEW_REGION_ORDER_WITH_CRYPTO if region in requested)
