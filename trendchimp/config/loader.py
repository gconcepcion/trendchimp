from __future__ import annotations

from trendchimp.config.settings import TrendChimpSettings


class ConfigurationError(Exception):
    """Raised when settings fail to load or validate."""


def load_settings() -> TrendChimpSettings:
    """Construct settings from environment / .env, re-wrapping validation errors."""
    from pydantic import ValidationError

    try:
        return TrendChimpSettings()
    except ValidationError as exc:
        messages = []
        for err in exc.errors():
            loc = ".".join(str(p) for p in err["loc"]) or "(root)"
            messages.append(f"  - {loc}: {err['msg']}")
        raise ConfigurationError(
            "Invalid trendchimp configuration:\n" + "\n".join(messages)
        ) from exc
