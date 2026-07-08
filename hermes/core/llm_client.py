"""
LLM Client for TokenHub API.

Provides both sync and async HTTP clients for LLM API calls.
"""
from __future__ import annotations

import json
import time
from typing import Any, Dict, Optional

import httpx

DEFAULT_BASE_URL = "https://tokenhub.tencentmaas.com/plan/v3"
DEFAULT_MODEL = "deepseek-v4-flash"
HTTP_TIMEOUT_SECONDS = 60.0


class TokenHubClient:
    """Synchronous client for TokenHub LLM API."""

    def __init__(self, api_key: str, base_url: str = DEFAULT_BASE_URL,
                 model: str = DEFAULT_MODEL, timeout: float = HTTP_TIMEOUT_SECONDS,
                 verbose: bool = True):
        if not api_key:
            raise ValueError("TOKENHUB_API_KEY is required")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.verbose = verbose
        self._client: Optional[httpx.Client] = None

    def _endpoint(self) -> str:
        return f"{self.base_url}/chat/completions"

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=self.timeout)
        return self._client

    def chat(self, messages, tools=None) -> Dict[str, Any]:
        """POST a chat completion request. Returns the raw JSON dict."""
        payload = {"model": self.model, "messages": messages, "stream": False}
        if tools:
            payload["tools"] = tools
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        endpoint = self._endpoint()

        t0 = time.perf_counter()
        client = self._get_client()
        try:
            resp = client.post(endpoint, headers=headers, json=payload)
            elapsed_ms = int((time.perf_counter() - t0) * 1000)
            if resp.status_code >= 400:
                raise RuntimeError(
                    f"TokenHub HTTP {resp.status_code}: {resp.text[:500]} "
                    f"(elapsed={elapsed_ms}ms)"
                )
            return resp.json()
        except Exception as exc:
            elapsed_ms = int((time.perf_counter() - t0) * 1000)
            if self.verbose:
                print(f"[LLM ERROR] ({elapsed_ms}ms) {exc}", flush=True)
            raise

    def close(self) -> None:
        """Close the underlying HTTP client."""
        if self._client:
            self._client.close()
            self._client = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


class AsyncTokenHubClient:
    """Asynchronous client for TokenHub LLM API (non-blocking).

    Use this for high-concurrency scenarios to avoid blocking the event loop.
    """

    def __init__(self, api_key: str, base_url: str = DEFAULT_BASE_URL,
                 model: str = DEFAULT_MODEL, timeout: float = HTTP_TIMEOUT_SECONDS,
                 verbose: bool = True):
        if not api_key:
            raise ValueError("TOKENHUB_API_KEY is required")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.verbose = verbose
        self._client: Optional[httpx.AsyncClient] = None

    def _endpoint(self) -> str:
        return f"{self.base_url}/chat/completions"

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client

    async def chat(self, messages, tools=None) -> Dict[str, Any]:
        """POST a chat completion request asynchronously."""
        payload = {"model": self.model, "messages": messages, "stream": False}
        if tools:
            payload["tools"] = tools
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        endpoint = self._endpoint()

        t0 = time.perf_counter()
        client = await self._get_client()
        try:
            resp = await client.post(endpoint, headers=headers, json=payload)
            elapsed_ms = int((time.perf_counter() - t0) * 1000)
            if resp.status_code >= 400:
                raise RuntimeError(
                    f"TokenHub HTTP {resp.status_code}: {resp.text[:500]} "
                    f"(elapsed={elapsed_ms}ms)"
                )
            return resp.json()
        except Exception as exc:
            elapsed_ms = int((time.perf_counter() - t0) * 1000)
            if self.verbose:
                print(f"[LLM ERROR] ({elapsed_ms}ms) {exc}", flush=True)
            raise

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        await self.close()
