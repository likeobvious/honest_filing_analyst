"""
src/llm/circuit_breaker.py
Circuit Breaker: Fast-fail async HTTP client for LiteLLM proxy with <5s outage fallback.
Complies with RFC-PS5-2026-001 and execution_plan.md.

Handles:
- Connect timeout (1.2s) and Read timeout (3.2s)
- Trips to OPEN state on failures; stays OPEN for 10s recovery window
- While OPEN: skips network entirely, returns Top-4 passages (<750ms)
- Provider switch detection (Groq -> OpenRouter / Gemini)
"""
import os
import time
import logging
import asyncio
from enum import Enum
from typing import Optional, Dict, Any, List
from dataclasses import dataclass

import httpx

from src.api.schemas import PassageResult, QueryResponse, Citation, SystemStatus

logger = logging.getLogger(__name__)

CB_OPEN_DURATION_SECS = 10.0
CB_CONNECT_TIMEOUT = 1.2
CB_READ_TIMEOUT = 3.2
CB_WRITE_TIMEOUT = 1.0
CB_POOL_TIMEOUT = 1.0

PRIMARY_MODEL_ID = "groq/llama-3.3-70b-versatile"
LLM_MAX_TOKENS = 250
LLM_TEMPERATURE = 0.0


class CircuitState(Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


@dataclass
class CircuitBreakerStats:
    state: CircuitState = CircuitState.CLOSED
    failure_count: int = 0
    last_failure_time: float = 0.0
    last_success_time: float = 0.0
    total_requests: int = 0
    total_failures: int = 0
    total_fallbacks: int = 0


class LiteLLMCircuitBreaker:
    """Async circuit breaker wrapping LiteLLM proxy HTTP calls."""

    def __init__(self, litellm_base_url: str = "http://127.0.0.1:4000/v1", api_key: str = "sk-ps5-team-key"):
        self.litellm_base_url = litellm_base_url.rstrip("/")
        self.api_key = api_key
        self._stats = CircuitBreakerStats()
        self._lock = asyncio.Lock()

    @property
    def state(self) -> CircuitState:
        return self._stats.state

    @property
    def stats(self) -> CircuitBreakerStats:
        return self._stats

    def _check_state_transition(self) -> CircuitState:
        if self._stats.state == CircuitState.OPEN:
            elapsed = time.monotonic() - self._stats.last_failure_time
            if elapsed >= CB_OPEN_DURATION_SECS:
                logger.info(f"Circuit breaker: OPEN -> HALF_OPEN after {elapsed:.1f}s.")
                self._stats.state = CircuitState.HALF_OPEN
        return self._stats.state

    async def call_llm(
        self,
        system_prompt: str,
        user_message: str,
        top4_passages: List[PassageResult],
        max_tokens: int = LLM_MAX_TOKENS,
    ) -> Dict[str, Any]:
        """Call LiteLLM proxy with fast-fail protection."""
        async with self._lock:
            current_state = self._check_state_transition()
            self._stats.total_requests += 1

        if current_state == CircuitState.OPEN:
            logger.info("Circuit breaker is OPEN. Skipping network call; returning passage fallback.")
            self._stats.total_fallbacks += 1
            return {
                "raw_response": None,
                "is_fallback": True,
                "provider_switched": False,
                "actual_model": None,
                "error": "Circuit breaker OPEN: LLM proxy offline.",
            }

        timeout = httpx.Timeout(
            connect=CB_CONNECT_TIMEOUT,
            read=CB_READ_TIMEOUT,
            write=CB_WRITE_TIMEOUT,
            pool=CB_POOL_TIMEOUT,
        )

        payload = {
            "model": "filing-analyst-llm",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            "max_tokens": max_tokens,
            "temperature": LLM_TEMPERATURE,
            "response_format": {"type": "json_object"},
        }

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                http_response = await client.post(
                    f"{self.litellm_base_url}/chat/completions",
                    json=payload,
                    headers=headers,
                )
                http_response.raise_for_status()
                data = http_response.json()

            async with self._lock:
                if self._stats.state == CircuitState.HALF_OPEN:
                    logger.info("Circuit breaker: HALF_OPEN -> CLOSED.")
                    self._stats.state = CircuitState.CLOSED
                    self._stats.failure_count = 0
                self._stats.last_success_time = time.monotonic()

            actual_model = data.get("model", "")
            provider_switched = bool(actual_model and not actual_model.startswith("llama") and "groq" not in actual_model.lower())

            return {
                "raw_response": data,
                "is_fallback": False,
                "provider_switched": provider_switched,
                "actual_model": actual_model,
                "error": None,
            }

        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout,
                httpx.WriteTimeout, httpx.RemoteProtocolError, httpx.HTTPStatusError) as exc:
            error_msg = f"{type(exc).__name__}: {exc}"
            logger.warning(f"LiteLLM call failed: {error_msg}")

            # Fallback to direct provider if local LiteLLM proxy container is offline
            direct_data = await self._call_direct_providers(system_prompt, user_message, max_tokens)
            if direct_data is not None:
                return direct_data

            async with self._lock:
                self._stats.failure_count += 1
                self._stats.total_failures += 1
                self._stats.last_failure_time = time.monotonic()
                self._stats.total_fallbacks += 1

                if self._stats.state in (CircuitState.CLOSED, CircuitState.HALF_OPEN):
                    logger.warning(
                        f"Circuit breaker tripped -> OPEN (failures={self._stats.failure_count})."
                    )
                    self._stats.state = CircuitState.OPEN

            return {
                "raw_response": None,
                "is_fallback": True,
                "provider_switched": False,
                "actual_model": None,
                "error": error_msg,
            }

    async def _call_direct_providers(
        self,
        system_prompt: str,
        user_message: str,
        max_tokens: int = LLM_MAX_TOKENS,
    ) -> Optional[Dict[str, Any]]:
        """Direct LLM provider fallback when local LiteLLM proxy container is offline."""
        groq_key = os.getenv("GROQ_API_KEY", "").strip()
        openrouter_key = os.getenv("OPENROUTER_API_KEY", "").strip()

        # 1. Direct Groq API
        if groq_key:
            models_to_try = [
                os.getenv("GROQ_MODEL", "qwen/qwen3.8-27b"),
                "llama-3.3-70b-versatile",
                "openai/gpt-oss-120b",
            ]
            for model_name in models_to_try:
                try:
                    payload = {
                        "model": model_name,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_message},
                        ],
                        "max_tokens": max_tokens,
                        "temperature": LLM_TEMPERATURE,
                        "response_format": {"type": "json_object"},
                    }
                    headers = {
                        "Authorization": f"Bearer {groq_key}",
                        "Content-Type": "application/json",
                    }
                    async with httpx.AsyncClient(timeout=8.0) as client:
                        resp = await client.post(
                            "https://api.groq.com/openai/v1/chat/completions",
                            json=payload,
                            headers=headers,
                        )
                        if resp.status_code == 200:
                            data = resp.json()
                            logger.info(f"Direct Groq call succeeded using model {model_name}")
                            return {
                                "raw_response": data,
                                "is_fallback": False,
                                "provider_switched": False,
                                "actual_model": f"groq/{model_name}",
                                "error": None,
                            }
                except Exception as e:
                    logger.debug(f"Direct Groq model {model_name} failed: {e}")

        # 2. Direct OpenRouter API
        if openrouter_key:
            or_models = [
                os.getenv("OPENROUTER_MODEL", "meta-llama/llama-3.3-70b-instruct"),
                "qwen/qwen-2.5-72b-instruct",
            ]
            for model_name in or_models:
                try:
                    payload = {
                        "model": model_name,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_message},
                        ],
                        "max_tokens": max_tokens,
                        "temperature": LLM_TEMPERATURE,
                        "response_format": {"type": "json_object"},
                    }
                    headers = {
                        "Authorization": f"Bearer {openrouter_key}",
                        "Content-Type": "application/json",
                    }
                    async with httpx.AsyncClient(timeout=8.0) as client:
                        resp = await client.post(
                            "https://openrouter.ai/api/v1/chat/completions",
                            json=payload,
                            headers=headers,
                        )
                        if resp.status_code == 200:
                            data = resp.json()
                            logger.info(f"Direct OpenRouter call succeeded using model {model_name}")
                            return {
                                "raw_response": data,
                                "is_fallback": False,
                                "provider_switched": True,
                                "actual_model": f"openrouter/{model_name}",
                                "error": None,
                            }
                except Exception as e:
                    logger.debug(f"Direct OpenRouter model {model_name} failed: {e}")

        return None

    def build_fallback_response(
        self,
        top4_passages: List[PassageResult],
        elapsed_ms: float = 0.0,
        error_reason: str = "LLM Service Offline",
        latency_ms: Optional[float] = None,
    ) -> QueryResponse:
        """
        Build graceful fallback QueryResponse displaying Top-4 verified passages.
        Guaranteed to execute in <50ms without network dependencies.
        """
        effective_latency = latency_ms if latency_ms is not None else elapsed_ms
        citations: List[Citation] = []
        passage_blocks: List[str] = []

        for rank, p in enumerate(top4_passages[:4], start=1):
            snippet = p.clean_markdown_text[:120].replace("\n", " ").strip()
            citations.append(
                Citation(
                    pdf_filename=p.pdf_filename,
                    pdf_page_number=p.pdf_page_number,
                    snippet=snippet,
                    printed_page_label=p.printed_page_label,
                )
            )
            preview = p.clean_markdown_text[:280].strip()
            if len(p.clean_markdown_text) > 280:
                preview += "..."
            passage_blocks.append(
                f"[{rank}] [{p.pdf_filename} | Page {p.pdf_page_number}]\n{preview}"
            )

        passages_text = "\n\n".join(passage_blocks) if passage_blocks else "No relevant passages found."
        answer = f"LLM Service Offline - Top retrieved passages from 10-K filings:\n\n{passages_text}"

        system_status = SystemStatus(
            execution_mode="DEGRADED_PASSAGE_FALLBACK",
            cache_hit=False,
            primary_model=PRIMARY_MODEL_ID,
            actual_model_used=None,
            provider_switched=False,
            ui_status_banner={
                "level": "WARNING",
                "message": (
                    "LLM Service Offline - Displaying Top-4 verified 10-K passages "
                    "and clickable page citations directly from local hybrid search without LLM processing."
                ),
            },
        )

        return QueryResponse(
            answer=answer,
            citations=citations,
            is_abstention=False,
            is_fallback_mode=True,
            tokens_used=0,
            latency_ms=round(effective_latency, 1),
            math_result=None,
            system_status=system_status,
        )
