"""The Analyst: runs a structured LLM call through any provider, with result caching
and cost tracking.

Every call is keyed by a hash of (provider, model, prompt version, system prompt,
input, schema). An identical request is answered from SQLite instead of the API, so
re-running a report costs nothing. Switching provider or model gives a fresh analysis.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import TypeVar

from pydantic import BaseModel

from value_engine import config
from value_engine.ai.prompts import PROMPT_VERSION
from value_engine.ai.providers import AIError, Completion, Provider
from value_engine.storage.db import Database

T = TypeVar("T", bound=BaseModel)

__all__ = ["AIError", "AIResult", "Analyst", "estimate_cost", "completion_cost"]


@dataclass
class AIResult:
    output: BaseModel
    input_tokens: int
    output_tokens: int
    cost_usd: float | None   # None when the model's price is not in config.AI_PRICES
    cached: bool


def _prices(model: str) -> dict[str, float] | None:
    return config.AI_PRICES.get(model)


def estimate_cost(model: str, input_chars: int, expected_output_tokens: int) -> float | None:
    """Rough pre-flight estimate: ~4 characters per token for English report text.
    Output includes thinking/reasoning tokens, which every provider bills as output."""
    prices = _prices(model)
    if prices is None:
        return None
    return (input_chars / 4 * prices["input"] + expected_output_tokens * prices["output"]) / 1e6


def completion_cost(model: str, c: Completion) -> float | None:
    prices = _prices(model)
    if prices is None:
        return None
    return (c.input_tokens * prices["input"] + c.output_tokens * prices["output"]
            + c.cache_read_tokens * prices.get("cache_read", prices["input"])
            + c.cache_write_tokens * prices.get("cache_write", prices["input"])) / 1e6


class Analyst:
    def __init__(self, db: Database, provider: Provider):
        self.db = db
        self.provider = provider
        self.spent_usd = 0.0      # this session, excluding cache hits and unknown prices
        self.unpriced_calls = 0   # calls whose cost could not be computed

    @property
    def label(self) -> str:
        return f"{self.provider.label} ({self.provider.model})"

    def run(self, *, kind: str, symbol: str, fiscal_year: int | None, system: str,
            user_content: str, schema: type[T]) -> AIResult:
        key = self._cache_key(system, user_content, schema)
        cached = self.db.get_ai_run(key)
        if cached:
            return AIResult(schema.model_validate_json(cached["output_json"]), cached["input_tokens"],
                            cached["output_tokens"], cached["cost_usd"], cached=True)

        completion = self.provider.complete(system, user_content, schema)
        cost = completion_cost(self.provider.model, completion)
        if cost is None:
            self.unpriced_calls += 1
        else:
            self.spent_usd += cost
        self.db.save_ai_run(key, symbol, kind, fiscal_year, f"{self.provider.name}:{self.provider.model}",
                            PROMPT_VERSION, completion.output.model_dump_json(), completion.input_tokens,
                            completion.output_tokens, cost)
        return AIResult(completion.output, completion.input_tokens, completion.output_tokens, cost, cached=False)

    def _cache_key(self, system: str, user_content: str, schema: type[BaseModel]) -> str:
        payload = json.dumps([self.provider.name, self.provider.model, config.AI_EFFORT, PROMPT_VERSION,
                              system, user_content, schema.model_json_schema()], sort_keys=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()
