from __future__ import annotations

from typing import Any, Type

from trendchimp.strategies.base import BaseStrategy


class StrategyRegistry:
    _registry: dict[str, Type[BaseStrategy]] = {}

    @classmethod
    def register(cls, strategy_cls: Type[BaseStrategy]) -> Type[BaseStrategy]:
        """Decorator that registers a strategy class by its name attribute."""
        if not strategy_cls.name:
            raise ValueError(f"Strategy {strategy_cls} must define a non-empty `name`")
        cls._registry[strategy_cls.name] = strategy_cls
        return strategy_cls

    @classmethod
    def get(cls, name: str, params: dict[str, Any] | None = None) -> BaseStrategy:
        if name not in cls._registry:
            available = ", ".join(sorted(cls._registry)) or "(none registered)"
            raise KeyError(f"Unknown strategy '{name}'. Available: {available}")
        return cls._registry[name](params=params)

    @classmethod
    def list_names(cls) -> list[str]:
        return sorted(cls._registry)
