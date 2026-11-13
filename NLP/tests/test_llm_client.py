"""Tests for LLM clients, the response cache, prompt files and answer parsing.

No network access is needed: a stub backend stands in for the real model.
It only exercises the plumbing and never produces experiment results.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from logad.config import LLMBackendConfig
from logad.llm.client import CachedClient, LLMResponse, ResponseCache, create_client
from logad.llm.parsing import extract_json, normalize_label, parse_classification
from logad.llm.prompts import load_prompt, parse_prompt


# Stub backend that echoes a fixed answer and counts how often it is called.
class StubBackend:
    name = "stub:model"

    def __init__(self, answer: str = '{"label": "normal"}') -> None:
        self.answer = answer
        self.calls = 0

    def settings(self) -> dict:
        return {"temperature": 0.0}

    def complete(self, messages) -> LLMResponse:
        self.calls += 1
        return LLMResponse(text=self.answer, latency_seconds=0.25)


MESSAGES = [{"role": "user", "content": "Log: data TLB error interrupt"}]

# ----------------------------------------------------------------------- cache


# The key depends on model, settings and messages, and nothing else.
def test_cache_key_is_deterministic() -> None:
    key = ResponseCache.make_key("m", {"t": 0}, MESSAGES)
    assert key == ResponseCache.make_key("m", {"t": 0}, list(MESSAGES))
    assert key != ResponseCache.make_key("m2", {"t": 0}, MESSAGES)
    assert key != ResponseCache.make_key("m", {"t": 1}, MESSAGES)
    assert key != ResponseCache.make_key("m", {"t": 0}, [{"role": "user", "content": "x"}])


# A repeated request is served from the cache, with the original latency.
def test_cached_client_hits_and_misses(tmp_path: Path) -> None:
    backend = StubBackend()
    client = CachedClient(backend, ResponseCache(tmp_path / "cache.jsonl"))
    first = client.complete(MESSAGES)
    second = client.complete(MESSAGES)
    assert backend.calls == 1
    assert not first.cached and second.cached
    assert second.text == first.text and second.latency_seconds == 0.25
    client.complete([{"role": "user", "content": "another log"}])
    assert backend.calls == 2 and (client.hits, client.misses) == (1, 2)


# The cache file survives across processes (a new ResponseCache reloads it).
def test_cache_persists_to_disk(tmp_path: Path) -> None:
    path = tmp_path / "cache.jsonl"
    CachedClient(StubBackend(), ResponseCache(path)).complete(MESSAGES)
    backend = StubBackend(answer="different")
    reloaded = CachedClient(backend, ResponseCache(path))
    assert reloaded.complete(MESSAGES).text == '{"label": "normal"}'
    assert backend.calls == 0


# Misconfiguration produces clear errors before any request is sent.
def test_create_client_errors(tmp_path: Path, monkeypatch) -> None:
    with pytest.raises(ValueError, match="Unknown LLM backend"):
        create_client(LLMBackendConfig(backend="nope"), tmp_path)
    monkeypatch.delenv("MISSING_KEY_FOR_TEST", raising=False)
    with pytest.raises(RuntimeError, match="MISSING_KEY_FOR_TEST"):
        create_client(LLMBackendConfig(api_key_env="MISSING_KEY_FOR_TEST"), tmp_path)


# Local OpenAI-compatible servers (Ollama, vLLM) work without an API key.
def test_local_server_needs_no_key(tmp_path: Path, monkeypatch) -> None:
    pytest.importorskip("openai")
    monkeypatch.delenv("MISSING_KEY_FOR_TEST", raising=False)
    config = LLMBackendConfig(
        model="qwen2.5:7b-instruct",
        base_url="http://localhost:11434/v1",
        api_key_env="MISSING_KEY_FOR_TEST",
    )
    client = create_client(config, tmp_path)
    assert client.name == "openai:qwen2.5:7b-instruct"
    assert client.cache.path.name == "openai_qwen2.5_7b-instruct.jsonl"


# --------------------------------------------------------------------- prompts


# A prompt file is split into system and user templates and filled in.
def test_parse_and_render_prompt() -> None:
    prompt = parse_prompt(
        "demo", '### SYSTEM\nYou are an expert.\n### USER\nLog: $log\nAnswer {"label": ...}\n'
    )
    messages = prompt.render(log="generating core.<*>")
    assert messages[0] == {"role": "system", "content": "You are an expert."}
    assert messages[1]["content"] == 'Log: generating core.<*>\nAnswer {"label": ...}'
    with pytest.raises(KeyError):
        prompt.render()  # $log is required


# Files without a user section are rejected; files load from a directory.
def test_prompt_files(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="USER"):
        parse_prompt("bad", "### SYSTEM\nonly system")
    (tmp_path / "p.txt").write_text("### USER\nHi $name", encoding="utf-8")
    assert load_prompt("p", tmp_path).render(name="Bo")[-1]["content"] == "Hi Bo"


# --------------------------------------------------------------------- parsing


# JSON is found inside code fences and prose; braces inside strings are fine.
@pytest.mark.parametrize(
    "text",
    [
        '{"label": "abnormal", "reason": "uses {braces}"}',
        'Sure! ```json\n{"label": "abnormal", "reason": "uses {braces}"}\n```',
        'Answer: {not json} then {"label": "abnormal", "reason": "uses {braces}"} done',
    ],
)
def test_extract_json(text: str) -> None:
    assert extract_json(text) == {"label": "abnormal", "reason": "uses {braces}"}


# Text without a JSON object yields None.
def test_extract_json_missing() -> None:
    assert extract_json("no json here") is None
    assert extract_json("[1, 2]") is None


# Label spellings are normalised to 1 (abnormal) / 0 (normal).
@pytest.mark.parametrize(
    ("value", "expected"),
    [("abnormal", 1), ("Anomalous", 1), (" normal ", 0), (True, 1), (0, 0), ("maybe", None)],
)
def test_normalize_label(value, expected) -> None:
    assert normalize_label(value) == expected


# Full answers: JSON first, then a word-level fallback, else None.
def test_parse_classification() -> None:
    parsed = parse_classification('{"label": "abnormal", "rule": "3", "reason": "TLB error"}')
    assert (parsed.label, parsed.rule, parsed.reason, parsed.from_json) == (1, 3, "TLB error", True)

    fallback = parse_classification("This log is abnormal because it reports a failure.")
    assert fallback.label == 1 and not fallback.from_json
    assert parse_classification("Looks normal to me.").label == 0
    assert parse_classification("I cannot tell.").label is None
