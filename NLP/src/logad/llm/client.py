"""LLM clients with a persistent response cache.

Every backend implements one method::

    complete(messages) -> LLMResponse

where ``messages`` is a chat transcript (``[{"role": "system" | "user", "content": ...}]``).

Backends
--------
``OpenAICompatibleClient``  any server that speaks the OpenAI chat protocol:
                            Ollama, vLLM, LM Studio... serving the open
                            Qwen2.5-7B-Instruct (the default for both rule
                            induction and deduction, no API key needed), or
                            the OpenAI API itself (e.g. GPT-4o-mini, LogRules'
                            original rule generator)
``TransformersClient``      a Hugging Face model loaded in-process, e.g. on a
                            Colab GPU

Caching
-------
``CachedClient`` wraps any backend and stores each (model, prompt, settings)
-> response pair in a JSONL file. Re-running an experiment therefore costs
nothing, and the cache doubles as a complete record of every prompt sent and
every answer received. The latency of the original call is stored too, so
inference time can still be reported for cached runs.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from logad.config import LLMBackendConfig
from logad.utils import ensure_dir, get_logger

logger = get_logger(__name__)

Messages = list[dict[str, str]]


# One model answer plus bookkeeping for timing and cache statistics.
@dataclass(frozen=True)
class LLMResponse:
    text: str
    latency_seconds: float  # time the backend took (for cached answers: the original call)
    cached: bool = False


# Interface every backend implements (structural typing: no base class needed).
class LLMClient(Protocol):
    # Human-readable identity, e.g. "openai:gpt-4o-mini"; part of the cache key.
    name: str

    # Generate the assistant reply to a chat transcript.
    def complete(self, messages: Messages) -> LLMResponse: ...

    # Generation settings that change the output; part of the cache key.
    def settings(self) -> dict[str, Any]: ...


# Client for the OpenAI chat-completions API and compatible servers.
class OpenAICompatibleClient:
    def __init__(self, config: LLMBackendConfig) -> None:
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover - depends on the environment
            raise ImportError("Install the LLM extras: pip install -e '.[llm]'") from exc

        api_key = os.environ.get(config.api_key_env)
        if not api_key:
            if config.base_url is None:
                raise RuntimeError(
                    f"Set the {config.api_key_env} environment variable to use {config.model}"
                )
            api_key = "not-needed"  # local servers such as Ollama ignore the key
        self.config = config
        self.name = f"openai:{config.model}"
        self._client = OpenAI(
            api_key=api_key,
            base_url=config.base_url,
            timeout=config.timeout_seconds,
            max_retries=5,  # the SDK retries rate limits / transient errors with backoff
        )

    # Settings that influence the generated text.
    def settings(self) -> dict[str, Any]:
        return {
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
            "json_mode": self.config.json_mode,
            "seed": self.config.seed,
        }

    # Send the transcript and return the first choice's text.
    def complete(self, messages: Messages) -> LLMResponse:
        kwargs: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
            "seed": self.config.seed,
        }
        if self.config.json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        start = time.perf_counter()
        response = self._client.chat.completions.create(**kwargs)
        latency = time.perf_counter() - start
        return LLMResponse(text=response.choices[0].message.content or "", latency_seconds=latency)


# Client that runs a Hugging Face chat model locally with transformers.
class TransformersClient:
    def __init__(self, config: LLMBackendConfig) -> None:
        try:
            import torch
            from transformers import AutoModelForCausalLM, AutoTokenizer
        except ImportError as exc:  # pragma: no cover - depends on the environment
            raise ImportError("Install the LLM extras: pip install -e '.[llm]'") from exc

        self.config = config
        self.name = f"transformers:{config.model}"
        model_kwargs: dict[str, Any] = {"device_map": "auto", "torch_dtype": "auto"}
        if config.load_in_4bit:
            from transformers import BitsAndBytesConfig

            # 4-bit weights let a 7B model run on a single 16 GB GPU.
            model_kwargs["quantization_config"] = BitsAndBytesConfig(
                load_in_4bit=True, bnb_4bit_compute_dtype=torch.float16
            )
        logger.info("Loading %s (this can take a few minutes)...", config.model)
        self._torch = torch
        self._tokenizer = AutoTokenizer.from_pretrained(config.model)
        self._model = AutoModelForCausalLM.from_pretrained(config.model, **model_kwargs)
        self._model.eval()

    # Settings that influence the generated text.
    def settings(self) -> dict[str, Any]:
        return {
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
            "load_in_4bit": self.config.load_in_4bit,
        }

    # Apply the model's chat template, generate, and decode only the new tokens.
    def complete(self, messages: Messages) -> LLMResponse:
        inputs = self._tokenizer.apply_chat_template(
            messages, add_generation_prompt=True, return_tensors="pt", return_dict=True
        ).to(self._model.device)
        sampling = self.config.temperature > 0
        start = time.perf_counter()
        with self._torch.no_grad():
            output = self._model.generate(
                **inputs,
                max_new_tokens=self.config.max_tokens,
                do_sample=sampling,
                temperature=self.config.temperature if sampling else None,
                top_p=None,
                top_k=None,
                pad_token_id=self._tokenizer.eos_token_id,
            )
        latency = time.perf_counter() - start
        new_tokens = output[0, inputs["input_ids"].shape[1] :]
        text = self._tokenizer.decode(new_tokens, skip_special_tokens=True)
        return LLMResponse(text=text, latency_seconds=latency)


# Append-only JSONL store of past LLM calls, keyed by a hash of the request.
class ResponseCache:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        ensure_dir(self.path.parent)
        self._entries: dict[str, dict[str, Any]] = {}
        if self.path.exists():
            with self.path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    if line.strip():
                        entry = json.loads(line)
                        self._entries[entry["key"]] = entry

    # Stable SHA-256 key of everything that determines the answer.
    @staticmethod
    def make_key(model: str, settings: dict[str, Any], messages: Messages) -> str:
        payload = json.dumps(
            {"model": model, "settings": settings, "messages": messages},
            sort_keys=True,
            ensure_ascii=False,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    # Return the stored entry for `key`, or None.
    def get(self, key: str) -> dict[str, Any] | None:
        return self._entries.get(key)

    # Store a new entry in memory and append it to the file immediately, so
    # an interrupted run keeps everything it already paid for.
    def put(self, entry: dict[str, Any]) -> None:
        self._entries[entry["key"]] = entry
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def __len__(self) -> int:
        return len(self._entries)


# Wraps a backend so identical requests are answered from the cache.
class CachedClient:
    def __init__(self, client: LLMClient, cache: ResponseCache) -> None:
        self.client = client
        self.cache = cache
        self.name = client.name
        self.hits = 0
        self.misses = 0

    # Same settings as the wrapped client.
    def settings(self) -> dict[str, Any]:
        return self.client.settings()

    # Return the cached answer if this exact request was made before,
    # otherwise call the backend and remember the answer.
    def complete(self, messages: Messages) -> LLMResponse:
        key = ResponseCache.make_key(self.client.name, self.client.settings(), messages)
        entry = self.cache.get(key)
        if entry is not None:
            self.hits += 1
            return LLMResponse(
                text=entry["response"], latency_seconds=entry["latency_seconds"], cached=True
            )
        self.misses += 1
        response = self.client.complete(messages)
        self.cache.put(
            {
                "key": key,
                "model": self.client.name,
                "settings": self.client.settings(),
                "messages": messages,
                "response": response.text,
                "latency_seconds": response.latency_seconds,
                "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }
        )
        return response


# Build a cached client from a backend config. Each model gets its own cache
# file under `cache_dir`, named after the backend and model.
def create_client(config: LLMBackendConfig, cache_dir: str | Path) -> CachedClient:
    if config.backend == "openai":
        backend: LLMClient = OpenAICompatibleClient(config)
    elif config.backend == "transformers":
        backend = TransformersClient(config)
    else:
        raise ValueError(f"Unknown LLM backend {config.backend!r}; use 'openai' or 'transformers'")
    safe_name = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in backend.name)
    return CachedClient(backend, ResponseCache(Path(cache_dir) / f"{safe_name}.jsonl"))
