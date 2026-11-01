"""One adapter per LLM provider, all with the same job:
system prompt + user content + Pydantic schema  ->  validated object + token counts.

How each provider guarantees the JSON shape:
  anthropic          native structured outputs (messages.parse)
  openai             native structured outputs (chat.completions.parse)
  google             JSON-schema response mode (response_json_schema)
  deepseek /         "JSON mode" only: the schema goes in the prompt, the reply is
  openai-compatible  validated here, and one retry is made if it doesn't fit

SDKs are imported lazily, so you only need the package for the provider you use.
Whatever the provider, ai/verify.py checks every quote against the report afterwards.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from value_engine import config

T = TypeVar("T", bound=BaseModel)


class AIError(RuntimeError):
    pass


@dataclass
class Completion:
    output: BaseModel
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


class Provider:
    name = ""

    def __init__(self, model: str | None = None, client=None):
        settings = config.AI_PROVIDERS[self.name]
        self.label = settings["label"]
        self.model = model or settings["model"]
        if not self.model:
            raise AIError(f"--model is required for the '{self.name}' provider")
        self.env_vars: list[str] = settings["env"]
        self._client = client

    @property
    def client(self):
        if self._client is None:
            self._client = self._make_client(self._api_key())
        return self._client

    def check_ready(self) -> None:
        """Fail fast (before downloading reports) if the key or endpoint is missing."""
        self._api_key()

    def complete(self, system: str, user: str, schema: type[T]) -> Completion:
        raise NotImplementedError

    def _make_client(self, api_key: str):
        raise NotImplementedError

    def _api_key(self) -> str:
        for var in self.env_vars:
            if os.environ.get(var):
                return os.environ[var]
        raise AIError(f"No API key for {self.label}: set the {' or '.join(self.env_vars)} environment "
                      "variable (each provider needs its own key), then restart VS Code.")


def _import(module: str, package: str):
    try:
        return __import__(module, fromlist=["_"])
    except ImportError as exc:
        raise AIError(f"The {package} package is not installed: run "
                      f".venv\\Scripts\\python -m pip install {package}") from exc


# --- Anthropic ---------------------------------------------------------------------
class AnthropicProvider(Provider):
    name = "anthropic"
    _FALLBACK_BETA = "server-side-fallback-2026-07-01"

    def _make_client(self, api_key):
        return _import("anthropic", "anthropic").Anthropic(api_key=api_key)

    def complete(self, system, user, schema):
        anthropic = _import("anthropic", "anthropic")
        params = dict(model=self.model, max_tokens=config.AI_MAX_TOKENS, system=system,
                      messages=[{"role": "user", "content": user}], output_format=schema,
                      output_config={"effort": config.AI_EFFORT})
        try:
            if config.AI_REFUSAL_FALLBACK:
                response = self.client.beta.messages.parse(betas=[self._FALLBACK_BETA], fallbacks="default", **params)
            else:
                response = self.client.messages.parse(**params)
        except anthropic.AuthenticationError as exc:
            raise AIError("ANTHROPIC_API_KEY is invalid.") from exc
        except anthropic.BadRequestError as exc:
            if "credit balance" in str(exc).lower():
                raise AIError("Your Anthropic account has no credit. Add credit at "
                              "console.anthropic.com > Plans & Billing, then re-run.") from exc
            raise AIError(f"Claude API rejected the request: {exc}") from exc
        except anthropic.RateLimitError as exc:
            raise AIError("Rate limited by the Claude API. Wait a minute and re-run; finished "
                          "reports are cached.") from exc
        except anthropic.APIConnectionError as exc:
            raise AIError(f"Could not reach the Claude API: {exc}") from exc
        except anthropic.APIStatusError as exc:
            raise AIError(f"Claude API error {exc.status_code}: {exc.message}") from exc

        if response.stop_reason == "refusal":
            raise AIError("Claude declined this request (safety filter). Try another provider with --provider.")
        if response.stop_reason == "max_tokens":
            raise AIError(_TRUNCATED)
        if response.parsed_output is None:
            raise AIError("Claude returned no structured output")
        usage = response.usage
        return Completion(response.parsed_output, usage.input_tokens, usage.output_tokens,
                          getattr(usage, "cache_read_input_tokens", 0) or 0,
                          getattr(usage, "cache_creation_input_tokens", 0) or 0)


# --- OpenAI -------------------------------------------------------------------------
class OpenAIProvider(Provider):
    name = "openai"

    def _make_client(self, api_key):
        return _import("openai", "openai").OpenAI(api_key=api_key)

    def complete(self, system, user, schema):
        openai = _import("openai", "openai")
        extra = {"reasoning_effort": config.OPENAI_REASONING_EFFORT} if config.OPENAI_REASONING_EFFORT else {}
        try:
            response = self.client.chat.completions.parse(
                model=self.model, max_completion_tokens=config.AI_MAX_TOKENS, response_format=schema,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}], **extra)
        except openai.LengthFinishReasonError as exc:
            raise AIError(_TRUNCATED) from exc
        except openai.ContentFilterFinishReasonError as exc:
            raise AIError("OpenAI's content filter blocked the response. Try another provider.") from exc
        except Exception as exc:
            raise _openai_error(openai, self.label, exc) from exc

        message = response.choices[0].message
        if message.refusal:
            raise AIError(f"OpenAI declined: {message.refusal}")
        if message.parsed is None:
            raise AIError("OpenAI returned no structured output")
        usage = response.usage
        cached = getattr(getattr(usage, "prompt_tokens_details", None), "cached_tokens", 0) or 0
        return Completion(message.parsed, usage.prompt_tokens - cached, usage.completion_tokens, cached)


# --- Google Gemini --------------------------------------------------------------------
class GeminiProvider(Provider):
    name = "google"

    def _make_client(self, api_key):
        return _import("google.genai", "google-genai").Client(api_key=api_key)

    def complete(self, system, user, schema):
        genai_types = _import("google.genai.types", "google-genai")
        genai_errors = _import("google.genai.errors", "google-genai")
        try:
            response = self.client.models.generate_content(
                model=self.model, contents=user,
                config=genai_types.GenerateContentConfig(
                    system_instruction=system, max_output_tokens=config.AI_MAX_TOKENS,
                    response_mime_type="application/json",
                    response_json_schema=schema.model_json_schema()))
        except genai_errors.APIError as exc:
            if exc.code in (401, 403):
                raise AIError("GEMINI_API_KEY is invalid or lacks access to this model.") from exc
            if exc.code == 429:
                raise AIError("Gemini rate limit or quota reached (the free tier has daily limits). "
                              "Wait and re-run; finished reports are cached.") from exc
            raise AIError(f"Gemini API error {exc.code}: {exc.message}") from exc

        candidate = (response.candidates or [None])[0]
        reason = getattr(getattr(candidate, "finish_reason", None), "name", "")
        if reason == "MAX_TOKENS":
            raise AIError(_TRUNCATED)
        if reason in ("SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII", "RECITATION"):
            raise AIError(f"Gemini blocked the response ({reason}). Try another provider.")
        output = _validate(schema, response.text or "", self.label)
        usage = response.usage_metadata
        cached = usage.cached_content_token_count or 0
        # Gemini bills thinking ("thoughts") tokens as output
        output_tokens = (usage.candidates_token_count or 0) + (usage.thoughts_token_count or 0)
        return Completion(output, (usage.prompt_token_count or 0) - cached, output_tokens, cached)


# --- DeepSeek and any other OpenAI-compatible API (JSON mode) ----------------------------
class JsonModeProvider(Provider):
    """For APIs that guarantee *valid JSON* but not a schema: the schema is put in the
    prompt (DeepSeek also requires the word "json" there), the reply is validated, and
    one corrective retry is made."""
    name = "openai-compatible"

    def _base_url(self) -> str:
        settings = config.AI_PROVIDERS[self.name]
        url = settings.get("base_url") or os.environ.get(settings.get("base_url_env", ""), "")
        if not url:
            raise AIError(f"Set the {settings['base_url_env']} environment variable to the API's base URL.")
        return url

    def check_ready(self) -> None:
        self._api_key()
        self._base_url()

    def _make_client(self, api_key):
        return _import("openai", "openai").OpenAI(api_key=api_key, base_url=self._base_url())

    def complete(self, system, user, schema):
        openai = _import("openai", "openai")
        schema_text = json.dumps(schema.model_json_schema(), indent=1)
        messages = [
            {"role": "system", "content": f"{system}\n\nReply with a single json object that validates "
                                          f"against this JSON Schema, and nothing else:\n{schema_text}"},
            {"role": "user", "content": user},
        ]
        total_in = total_out = cached = 0
        for attempt in range(2):
            try:
                response = self.client.chat.completions.create(
                    model=self.model, messages=messages, max_tokens=config.AI_MAX_TOKENS,
                    response_format={"type": "json_object"})
            except Exception as exc:
                raise _openai_error(openai, self.label, exc) from exc
            usage = response.usage
            hit = getattr(usage, "prompt_cache_hit_tokens", None) or getattr(
                getattr(usage, "prompt_tokens_details", None), "cached_tokens", 0) or 0
            total_in += usage.prompt_tokens - hit
            total_out += usage.completion_tokens
            cached += hit
            choice = response.choices[0]
            if choice.finish_reason == "length":
                raise AIError(_TRUNCATED)
            content = (choice.message.content or "").strip()
            try:
                return Completion(schema.model_validate_json(content), total_in, total_out, cached)
            except ValidationError as exc:
                if attempt == 1:
                    raise AIError(f"{self.label} returned JSON that does not match the schema twice: "
                                  f"{exc.errors()[:3]}") from exc
                # Show the model its mistake once (DeepSeek sometimes returns blank content)
                messages += [{"role": "assistant", "content": content or "(empty)"},
                             {"role": "user", "content": "That reply was not a json object matching the "
                                                         f"schema. Errors: {exc.errors()[:5]}. Reply again "
                                                         "with only the corrected json object."}]
        raise AssertionError("unreachable")


class DeepSeekProvider(JsonModeProvider):
    name = "deepseek"


# --- shared helpers -----------------------------------------------------------------------
_TRUNCATED = (f"The response hit the {config.AI_MAX_TOKENS}-token limit. Raise AI_MAX_TOKENS in "
              "config.py (or lower AI_EFFORT for Claude).")


def _validate(schema: type[T], text: str, label: str) -> T:
    try:
        return schema.model_validate_json(text)
    except ValidationError as exc:
        raise AIError(f"{label} returned output that does not match the schema: {exc.errors()[:3]}") from exc


def _openai_error(openai, label: str, exc: Exception) -> AIError:
    """Errors from the OpenAI SDK, which DeepSeek and other compatible APIs also use."""
    if isinstance(exc, openai.AuthenticationError):
        return AIError(f"The {label} API key is invalid.")
    if isinstance(exc, openai.RateLimitError):
        if "insufficient_quota" in str(exc) or "balance" in str(exc).lower():
            return AIError(f"Your {label} account has no credit or quota left. Add credit on the provider's "
                           "billing page, then re-run.")
        return AIError(f"Rate limited by {label}. Wait a minute and re-run; finished reports are cached.")
    if isinstance(exc, openai.APIConnectionError):
        return AIError(f"Could not reach the {label} API: {exc}")
    if isinstance(exc, openai.APIStatusError):
        if exc.status_code == 402:
            return AIError(f"Your {label} account has insufficient balance. Top up, then re-run.")
        return AIError(f"{label} API error {exc.status_code}: {exc.message}")
    return AIError(f"{label} request failed: {exc}")


PROVIDERS: dict[str, type[Provider]] = {
    "anthropic": AnthropicProvider,
    "openai": OpenAIProvider,
    "google": GeminiProvider,
    "deepseek": DeepSeekProvider,
    "openai-compatible": JsonModeProvider,
}


def make_provider(name: str, model: str | None = None, client=None) -> Provider:
    if name not in PROVIDERS:
        raise AIError(f"Unknown provider '{name}'. Choose from: {', '.join(PROVIDERS)}")
    return PROVIDERS[name](model=model, client=client)
