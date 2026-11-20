# -*- coding: utf-8 -*-
"""Deferred ``litellm`` import.

Importing ``litellm`` costs roughly **208 MB RSS** at import time (measured in
this repository's runtime image: ``import litellm`` alone took a fresh Python
process from 10 MB to 219 MB). The Web/API process only needs it when an
analysis or a chat generation actually runs, so this module exposes:

* ``litellm`` — a proxy that imports the real module on first attribute access
  or first call, keeping ``litellm.completion(...)`` and
  ``litellm.exceptions.X`` working unchanged;
* ``Router`` — a factory that imports on construction, so ``Router(...)`` and
  annotations keep working.

Anything that patches these symbols (tests, callers) still hits the real
module, because attribute writes are forwarded.
"""

from __future__ import annotations

import importlib
import threading
from typing import Any

_lock = threading.Lock()
_module: Any = None


def get_litellm() -> Any:
    """Import and memoize the ``litellm`` module on first use."""

    global _module
    if _module is None:
        with _lock:
            if _module is None:
                module = importlib.import_module("litellm")
                # 本项目不注册 LiteLLM success callback，用量从响应自身统计；
                # 关掉它默认的 streaming 日志，避免每个 chunk 打日志。
                try:
                    module.disable_streaming_logging = True
                except Exception:  # pragma: no cover - defensive
                    pass
                _module = module
    return _module


class _LitellmProxy:
    """Module-like proxy that defers the import until something is used."""

    __slots__ = ()

    def __getattr__(self, name: str) -> Any:
        return getattr(get_litellm(), name)

    def __setattr__(self, name: str, value: Any) -> None:
        setattr(get_litellm(), name, value)

    def __delattr__(self, name: str) -> None:
        delattr(get_litellm(), name)

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return get_litellm()(*args, **kwargs)

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        if _module is None:
            return "<lazy litellm (not imported yet)>"
        return repr(_module)


litellm = _LitellmProxy()


def Router(*args: Any, **kwargs: Any) -> Any:
    """Construct ``litellm.Router`` without importing litellm up front."""

    return get_litellm().Router(*args, **kwargs)


__all__ = ["litellm", "Router", "get_litellm"]
