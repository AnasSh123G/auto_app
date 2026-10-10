"""OpenRouter model prices, fetched from its public model list instead of typed into the config.

``GET https://openrouter.ai/api/v1/models`` needs no key and lists ``pricing.prompt`` / ``completion`` /
``input_cache_read`` in USD per token. The answer is cached in the workspace and refreshed at most once a day
(at the start of a run and when the statistics page is opened); a failed refresh keeps the cached prices.
Prices you set in ``llm.prices`` always win, and the shipped table is only the last fallback.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from autoapp.llm.settings import LLMSettings, Price

log = logging.getLogger("autoapp")

URL = "https://openrouter.ai/api/v1/models"
MAX_AGE = timedelta(hours=24)


def _per_million(value: Any) -> float | None:
    try:
        number = float(value) * 1_000_000
    except (TypeError, ValueError):
        return None
    return round(number, 6) if number >= 0 else None  # "-1" marks a price that depends on the routed model


def parse_models(payload: Any) -> dict[str, Price]:
    """``{model id: Price}`` (USD per 1M tokens) from an OpenRouter model list; unpriced entries are left out."""
    prices: dict[str, Price] = {}
    for entry in (payload.get("data") if isinstance(payload, dict) else None) or []:
        pricing = entry.get("pricing") if isinstance(entry, dict) else None
        if not isinstance(pricing, dict) or not isinstance(entry.get("id"), str):
            continue
        prompt, completion = _per_million(pricing.get("prompt")), _per_million(pricing.get("completion"))
        if prompt is None or completion is None:
            continue
        prices[entry["id"]] = Price(input=prompt, output=completion,
                                    cached_input=_per_million(pricing.get("input_cache_read")))
    return prices


def load_cached(path: Path) -> tuple[dict[str, Price], datetime | None]:
    """The cached prices and when they were fetched; empty when there is no usable cache."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        fetched = datetime.fromisoformat(data["fetched_at"])
        prices = {model: Price(**entry) for model, entry in data["models"].items()}
    except (OSError, ValueError, KeyError, TypeError):
        return {}, None
    return prices, fetched


def uses_openrouter(settings: LLMSettings) -> bool:
    from autoapp.llm.base import TASKS

    return any(settings.resolve(task)[0] == "openrouter" for task in TASKS)


def refresh(path: Path, settings: LLMSettings, *, force: bool = False, now: datetime | None = None,
            client: httpx.Client | None = None, timeout: float = 10.0) -> str | None:
    """Fetch the prices when OpenRouter is in use and the cache is older than a day. Never raises.

    Returns a short note when a fetch failed (the old prices stay in use), else ``None``.
    """
    if not uses_openrouter(settings):
        return None
    now = now or datetime.now(UTC)
    _, fetched = load_cached(path)
    if not force and fetched is not None and now - fetched < MAX_AGE:
        return None
    own = client is None
    http = client or httpx.Client(timeout=timeout, follow_redirects=False,
                                  headers={"User-Agent": "autoapp (job application helper)"})
    try:
        response = http.get(URL)
        response.raise_for_status()
        prices = parse_models(response.json())
        if not prices:
            return "OpenRouter returned no prices; keeping the saved ones"
        document = {"fetched_at": now.isoformat(timespec="seconds"),
                    "models": {m: p.model_dump(exclude_none=True) for m, p in sorted(prices.items())}}
        path.write_text(json.dumps(document, indent=0), encoding="utf-8")
        return None
    except (httpx.HTTPError, ValueError, OSError) as err:
        log.warning("could not fetch OpenRouter prices: %s", err)
        return f"could not fetch OpenRouter prices ({type(err).__name__}); keeping the saved ones"
    finally:
        if own:
            http.close()


def auto_refresh(path: Path, settings: LLMSettings) -> str | None:
    """``refresh`` for the commands; ``AUTOAPP_NO_PRICE_FETCH=1`` switches the network call off (tests, offline use)."""
    if os.environ.get("AUTOAPP_NO_PRICE_FETCH") == "1":
        return None
    return refresh(path, settings)
