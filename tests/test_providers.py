"""Each provider adapter against a fake of its SDK: request shape, parsing, errors, cost."""
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from value_engine import config
from value_engine.ai.client import Analyst, completion_cost, estimate_cost
from value_engine.ai.providers import (AIError, AnthropicProvider, DeepSeekProvider, GeminiProvider,
                                       JsonModeProvider, OpenAIProvider, make_provider)
from value_engine.storage.db import Database


class Opinion(BaseModel):
    company: str
    opinion: str


GOOD = Opinion(company="ABC Ltd", opinion="unmodified")


# --- fakes shaped like each SDK's response objects ----------------------------------
class FakeOpenAI:
    def __init__(self, parse_result=None, create_contents=()):
        self.parse_result = parse_result
        self.contents = list(create_contents)
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(parse=self._parse, create=self._create))

    def _parse(self, **kwargs):
        self.calls.append(kwargs)
        return self.parse_result

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        content = self.contents.pop(0)
        return SimpleNamespace(
            choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content=content))],
            usage=SimpleNamespace(prompt_tokens=1000, completion_tokens=200, prompt_cache_hit_tokens=100))


def _openai_parsed(parsed, refusal=None):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(parsed=parsed, refusal=refusal))],
        usage=SimpleNamespace(prompt_tokens=1000, completion_tokens=300,
                              prompt_tokens_details=SimpleNamespace(cached_tokens=200)))


class FakeGemini:
    def __init__(self, text, finish="STOP"):
        self.text, self.finish, self.calls = text, finish, []
        self.models = SimpleNamespace(generate_content=self._generate)

    def _generate(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            text=self.text,
            candidates=[SimpleNamespace(finish_reason=SimpleNamespace(name=self.finish))],
            usage_metadata=SimpleNamespace(prompt_token_count=1000, cached_content_token_count=None,
                                           candidates_token_count=150, thoughts_token_count=350))


# --- tests ---------------------------------------------------------------------------
def test_openai_structured_output_and_cached_tokens():
    fake = FakeOpenAI(parse_result=_openai_parsed(GOOD))
    result = OpenAIProvider(client=fake).complete("sys", "user text", Opinion)
    assert result.output == GOOD
    assert (result.input_tokens, result.cache_read_tokens, result.output_tokens) == (800, 200, 300)
    call = fake.calls[0]
    assert call["response_format"] is Opinion and call["model"] == config.AI_PROVIDERS["openai"]["model"]
    assert call["messages"][0] == {"role": "system", "content": "sys"}


def test_openai_refusal_is_an_error():
    with pytest.raises(AIError, match="declined"):
        OpenAIProvider(client=FakeOpenAI(parse_result=_openai_parsed(None, refusal="no"))).complete("s", "u", Opinion)


def test_gemini_json_schema_mode_counts_thinking_as_output():
    fake = FakeGemini(GOOD.model_dump_json())
    result = GeminiProvider(client=fake).complete("sys", "user text", Opinion)
    assert result.output == GOOD
    assert result.output_tokens == 500  # 150 visible + 350 thinking
    cfg = fake.calls[0]["config"]
    assert cfg.response_mime_type == "application/json"
    assert cfg.response_json_schema == Opinion.model_json_schema()
    assert cfg.system_instruction == "sys"


def test_gemini_safety_block_and_truncation():
    with pytest.raises(AIError, match="blocked"):
        GeminiProvider(client=FakeGemini("", finish="SAFETY")).complete("s", "u", Opinion)
    with pytest.raises(AIError, match="token limit"):
        GeminiProvider(client=FakeGemini("{", finish="MAX_TOKENS")).complete("s", "u", Opinion)


def test_deepseek_json_mode_puts_schema_in_prompt():
    fake = FakeOpenAI(create_contents=[GOOD.model_dump_json()])
    result = DeepSeekProvider(client=fake).complete("sys", "user text", Opinion)
    assert result.output == GOOD
    call = fake.calls[0]
    assert call["response_format"] == {"type": "json_object"}
    system = call["messages"][0]["content"]
    assert "json" in system and '"opinion"' in system  # DeepSeek requires the word "json"
    assert (result.input_tokens, result.cache_read_tokens) == (900, 100)


def test_json_mode_retries_once_after_bad_or_blank_reply():
    fake = FakeOpenAI(create_contents=["   ", GOOD.model_dump_json()])  # DeepSeek can return blank content
    result = DeepSeekProvider(client=fake).complete("s", "u", Opinion)
    assert result.output == GOOD
    assert len(fake.calls) == 2 and "corrected json" in fake.calls[1]["messages"][-1]["content"]
    assert result.input_tokens == 1800  # both attempts are billed

    twice_bad = FakeOpenAI(create_contents=['{"company": "x"}', '{"company": "x"}'])
    with pytest.raises(AIError, match="does not match"):
        DeepSeekProvider(client=twice_bad).complete("s", "u", Opinion)


def test_missing_key_names_the_right_variable(monkeypatch):
    for var in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(AIError, match="GEMINI_API_KEY or GOOGLE_API_KEY"):
        make_provider("google").check_ready()
    monkeypatch.setenv("GOOGLE_API_KEY", "abc")
    make_provider("google").check_ready()  # either variable works


def test_generic_endpoint_needs_model_and_base_url(monkeypatch):
    with pytest.raises(AIError, match="--model is required"):
        make_provider("openai-compatible")
    monkeypatch.setenv("OPENAI_COMPATIBLE_API_KEY", "abc")
    monkeypatch.delenv("OPENAI_COMPATIBLE_BASE_URL", raising=False)
    with pytest.raises(AIError, match="OPENAI_COMPATIBLE_BASE_URL"):
        make_provider("openai-compatible", model="mistral-large").check_ready()
    assert isinstance(make_provider("openai-compatible", model="m"), JsonModeProvider)


def test_cost_and_cache_are_per_model():
    assert estimate_cost("gemini-3.8-flash", 4_000_000, 0) == pytest.approx(0.75)
    assert estimate_cost("some-unknown-model", 4_000, 100) is None

    db = Database(":memory:")
    openai_analyst = Analyst(db, OpenAIProvider(client=FakeOpenAI(parse_result=_openai_parsed(GOOD))))
    first = openai_analyst.run(kind="t", symbol="X", fiscal_year=None, system="s", user_content="u", schema=Opinion)
    assert first.cost_usd == pytest.approx(completion_cost("gpt-6.1-sol", SimpleNamespace(
        input_tokens=800, output_tokens=300, cache_read_tokens=200, cache_write_tokens=0)))
    # Same request through another provider is NOT served from the first provider's cache
    gemini = FakeGemini(GOOD.model_dump_json())
    Analyst(db, GeminiProvider(client=gemini)).run(kind="t", symbol="X", fiscal_year=None, system="s",
                                                   user_content="u", schema=Opinion)
    assert len(gemini.calls) == 1


def test_unknown_price_is_stored_as_null(monkeypatch):
    monkeypatch.setenv("OPENAI_COMPATIBLE_BASE_URL", "http://localhost:11434/v1")
    db = Database(":memory:")
    analyst = Analyst(db, JsonModeProvider(model="llama-local", client=FakeOpenAI(create_contents=[GOOD.model_dump_json()])))
    result = analyst.run(kind="t", symbol="X", fiscal_year=None, system="s", user_content="u", schema=Opinion)
    assert result.cost_usd is None and analyst.unpriced_calls == 1
    assert db.total_ai_cost() == 0


def test_anthropic_adapter_still_handles_refusal():
    fake_messages = SimpleNamespace(parse=lambda **kw: SimpleNamespace(
        parsed_output=None, stop_reason="refusal", usage=None))
    client = SimpleNamespace(messages=fake_messages, beta=SimpleNamespace(messages=fake_messages))
    with pytest.raises(AIError, match="--provider"):
        AnthropicProvider(client=client).complete("s", "u", Opinion)
