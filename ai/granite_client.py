"""
ai.granite_client
=================
IBM watsonx.ai client for the Granite family of models.

Uses direct REST API calls (no SDK dependency) with IBM IAM authentication:

1. POST https://iam.cloud.ibm.com/identity/token
       Exchange the IBM_API_KEY for a short-lived Bearer token.
2. POST {IBM_URL}/ml/v1/text/chat?version=2023-05-29
       Call the watsonx.ai chat endpoint with the project ID, model ID,
       and a single user message.

Token caching: the IAM token is cached in-process and refreshed automatically
when fewer than 60 seconds remain before expiry.

Retry logic: transient HTTP errors (429, 500, 502, 503, 504) and network
exceptions are retried with exponential back-off up to ``max_retries`` times.

Configuration
-------------
All settings are read from environment variables (see ``GraniteConfig``).
Required:

    IBM_API_KEY       — IBM Cloud IAM API key
    IBM_PROJECT_ID    — watsonx.ai project ID
    IBM_URL           — watsonx.ai endpoint URL
                        e.g. https://us-south.ml.cloud.ibm.com

Optional (with sensible defaults):

    IBM_MODEL_ID      — Granite model ID
                        (default: ibm/granite-4-h-small)
    IBM_MAX_TOKENS    — Maximum new tokens in the response (default: 4096)
    IBM_TEMPERATURE   — Sampling temperature 0–2 (default: 0.1)
    IBM_TIMEOUT       — Per-call timeout in seconds (default: 120)
    IBM_MAX_RETRIES   — Number of retry attempts on transient errors (default: 3)

REST API reference
------------------
    https://cloud.ibm.com/apidocs/watsonx-ai#text-chat
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, Optional

import requests

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_IAM_TOKEN_URL: str = "https://iam.cloud.ibm.com/identity/token"
_WATSONX_API_VERSION: str = "2023-05-29"
_WATSONX_CHAT_PATH: str = "/ml/v1/text/chat"

# Transient HTTP status codes that warrant a retry
_RETRYABLE_STATUS: frozenset[int] = frozenset({429, 500, 502, 503, 504})

# Refresh the token when fewer than this many seconds remain before expiry
_TOKEN_REFRESH_BUFFER_SECS: float = 60.0

# Back-off base in seconds (doubles each attempt: 1s, 2s, 4s, …)
_BACKOFF_BASE: float = 1.0


# ---------------------------------------------------------------------------
# Typed configuration
# ---------------------------------------------------------------------------


class GraniteConfig:
    """Reads and validates IBM watsonx.ai credentials from the environment.

    Raises
    ------
    EnvironmentError
        If any required variable (``IBM_API_KEY``, ``IBM_PROJECT_ID``,
        ``IBM_URL``) is missing or empty.
    """

    DEFAULT_MODEL: str = "ibm/granite-4-h-small"

    # Required
    api_key: str
    project_id: str
    url: str

    # Optional with defaults
    model_id: str
    max_new_tokens: int
    temperature: float
    timeout: float
    max_retries: int

    def __init__(
        self,
        api_key: Optional[str] = None,
        project_id: Optional[str] = None,
        url: Optional[str] = None,
        model_id: Optional[str] = None,
        max_new_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        timeout: Optional[float] = None,
        max_retries: Optional[int] = None,
    ) -> None:
        self.api_key = api_key or os.environ.get("IBM_API_KEY", "")
        self.project_id = project_id or os.environ.get("IBM_PROJECT_ID", "")
        self.url = (url or os.environ.get("IBM_URL", "")).rstrip("/")

        missing = [
            name
            for name, val in [
                ("IBM_API_KEY", self.api_key),
                ("IBM_PROJECT_ID", self.project_id),
                ("IBM_URL", self.url),
            ]
            if not val
        ]
        if missing:
            raise EnvironmentError(
                f"Missing required IBM watsonx.ai environment variable(s): "
                f"{', '.join(missing)}.  "
                f"Set them in your environment or in a .env file."
            )

        self.model_id = model_id or os.environ.get("IBM_MODEL_ID", self.DEFAULT_MODEL)
        self.max_new_tokens = int(
            max_new_tokens if max_new_tokens is not None
            else os.environ.get("IBM_MAX_TOKENS", 4096)
        )
        self.temperature = float(
            temperature if temperature is not None
            else os.environ.get("IBM_TEMPERATURE", 0.1)
        )
        self.timeout = float(
            timeout if timeout is not None
            else os.environ.get("IBM_TIMEOUT", 120)
        )
        self.max_retries = int(
            max_retries if max_retries is not None
            else os.environ.get("IBM_MAX_RETRIES", 3)
        )

    def __repr__(self) -> str:  # never leak the key
        return (
            f"GraniteConfig(url={self.url!r}, model_id={self.model_id!r}, "
            f"project_id={self.project_id[:8]!r}…)"
        )


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------


class GraniteClientError(RuntimeError):
    """Raised when the Granite call cannot be completed after all retries."""

    def __init__(self, message: str, last_error: Optional[Exception] = None) -> None:
        super().__init__(message)
        self.last_error = last_error


class GraniteEmptyResponseError(GraniteClientError):
    """Raised when the model returns an empty or whitespace-only response."""


# ---------------------------------------------------------------------------
# IAM token cache
# ---------------------------------------------------------------------------


class _IamToken:
    """Holds a cached IAM Bearer token and its expiry timestamp."""

    def __init__(self, access_token: str, expires_in: int) -> None:
        self.access_token: str = access_token
        self._expires_at: float = time.monotonic() + expires_in

    def is_valid(self) -> bool:
        """Return True if the token won't expire within the refresh buffer."""
        return (self._expires_at - time.monotonic()) > _TOKEN_REFRESH_BUFFER_SECS


# ---------------------------------------------------------------------------
# GraniteClient
# ---------------------------------------------------------------------------


class GraniteClient:
    """Direct REST client for IBM watsonx.ai text generation.

    Handles IAM token exchange, token caching/refresh, retry logic, and
    structured logging.  The public interface is identical to the previous
    SDK-based implementation so no other module needs to change.

    Parameters
    ----------
    config:
        ``GraniteConfig`` instance.  When omitted one is created from the
        current environment.
    """

    def __init__(self, config: Optional[GraniteConfig] = None) -> None:
        self._config: GraniteConfig = config or GraniteConfig()
        self._token: Optional[_IamToken] = None
        self._session: requests.Session = requests.Session()
        self._session.headers.update({"Content-Type": "application/json"})
        logger.info(
            "GraniteClient ready — model=%s project=%s… url=%s",
            self._config.model_id,
            self._config.project_id[:8],
            self._config.url,
        )

    @property
    def model_id(self) -> str:
        """The Granite model ID this client is configured to use."""
        return self._config.model_id

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate(self, prompt: str) -> str:
        """Send ``prompt`` to Granite and return the generated text.

        Automatically retries up to ``config.max_retries`` times on
        transient errors with exponential back-off.

        Parameters
        ----------
        prompt:
            The complete prompt string to send to the model.

        Returns
        -------
        str
            Raw generated text from the model (not yet parsed).

        Raises
        ------
        GraniteClientError
            After all retry attempts are exhausted.
        GraniteEmptyResponseError
            If the model returns an empty body even after retries.
        """
        attempt = 0
        last_exc: Optional[Exception] = None

        while attempt <= self._config.max_retries:
            try:
                return self._call(prompt)
            except GraniteEmptyResponseError:
                raise  # empty response is not retried — it's a prompt issue
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                attempt += 1
                if attempt > self._config.max_retries:
                    break
                backoff = _BACKOFF_BASE * (2 ** (attempt - 1))
                logger.warning(
                    "Granite call failed (attempt %d/%d): %s — retrying in %.1fs",
                    attempt,
                    self._config.max_retries,
                    exc,
                    backoff,
                )
                time.sleep(backoff)

        raise GraniteClientError(
            f"Granite call failed after {self._config.max_retries} retries: {last_exc}",
            last_error=last_exc,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_token(self) -> str:
        """Return a valid IAM Bearer token, refreshing it when necessary."""
        if self._token is None or not self._token.is_valid():
            self._token = self._fetch_iam_token()
        return self._token.access_token

    def _fetch_iam_token(self) -> _IamToken:
        """Exchange the API key for an IAM access token via IBM IAM endpoint."""
        logger.debug("Fetching IAM token from %s", _IAM_TOKEN_URL)
        try:
            resp = requests.post(
                _IAM_TOKEN_URL,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                data={
                    "grant_type": "urn:ibm:params:oauth:grant-type:apikey",
                    "apikey": self._config.api_key,
                },
                timeout=30,
            )
        except requests.RequestException as exc:
            raise GraniteClientError(
                f"Network error fetching IAM token: {exc}", last_error=exc
            ) from exc

        if not resp.ok:
            raise GraniteClientError(
                f"IAM token request failed — HTTP {resp.status_code}: {resp.text[:200]}"
            )

        payload: Dict[str, Any] = resp.json()
        access_token: str = payload.get("access_token", "")
        expires_in: int = int(payload.get("expires_in", 3600))

        if not access_token:
            raise GraniteClientError("IAM response did not contain an access_token.")

        logger.debug("IAM token obtained (expires_in=%ds)", expires_in)
        return _IamToken(access_token=access_token, expires_in=expires_in)

    def _call(self, prompt: str) -> str:
        """Single (non-retried) REST call to the watsonx.ai chat endpoint.

        Request schema  (POST /ml/v1/text/chat):
            {
              "model_id": "...",
              "project_id": "...",
              "messages": [{"role": "user", "content": "<prompt>"}],
              "parameters": { "max_new_tokens": ..., "temperature": ..., ... }
            }

        Response schema:
            {
              "choices": [
                {
                  "message": {"role": "assistant", "content": "<reply>"},
                  "finish_reason": "stop"
                }
              ],
              "usage": { "prompt_tokens": ..., "completion_tokens": ..., ... }
            }
        """
        token_estimate = int(len(prompt.split()) * 1.3)
        logger.info(
            "Sending prompt to Granite — model=%s, chars=%d, ~%d tokens",
            self._config.model_id,
            len(prompt),
            token_estimate,
        )
        start = time.monotonic()

        token = self._get_token()
        endpoint = (
            f"{self._config.url}{_WATSONX_CHAT_PATH}"
            f"?version={_WATSONX_API_VERSION}"
        )
        body: Dict[str, Any] = {
            "model_id": self._config.model_id,
            "project_id": self._config.project_id,
            "messages": [
                {"role": "user", "content": prompt},
            ],
            "parameters": {
                "max_new_tokens": self._config.max_new_tokens,
                "temperature": self._config.temperature,
                "repetition_penalty": 1.05,
            },
        }

        try:
            resp = self._session.post(
                endpoint,
                json=body,
                headers={"Authorization": f"Bearer {token}"},
                timeout=self._config.timeout,
            )
        except requests.RequestException as exc:
            raise GraniteClientError(
                f"Network error calling watsonx.ai: {exc}", last_error=exc
            ) from exc

        elapsed = (time.monotonic() - start) * 1000

        if resp.status_code in _RETRYABLE_STATUS:
            raise GraniteClientError(
                f"watsonx.ai returned retryable HTTP {resp.status_code}: {resp.text[:200]}"
            )

        if not resp.ok:
            raise GraniteClientError(
                f"watsonx.ai error — HTTP {resp.status_code}: {resp.text[:400]}"
            )

        data: Dict[str, Any] = resp.json()

        # Response shape: {"choices": [{"message": {"role": "assistant", "content": "..."}, "finish_reason": "stop"}]}
        choices = data.get("choices", [])
        generated_text: str = (
            choices[0].get("message", {}).get("content", "") if choices else ""
        )
        finish_reason: str = choices[0].get("finish_reason", "unknown") if choices else "none"

        # Surface usage stats when available
        usage = data.get("usage", {})
        if usage:
            logger.info(
                "[granite] USAGE  prompt_tokens=%s completion_tokens=%s total_tokens=%s",
                usage.get("prompt_tokens", "?"),
                usage.get("completion_tokens", "?"),
                usage.get("total_tokens", "?"),
            )

        logger.info(
            "Granite response received in %.0f ms (%d chars, finish_reason=%s)",
            elapsed,
            len(generated_text),
            finish_reason,
        )

        if not generated_text or not generated_text.strip():
            raise GraniteEmptyResponseError(
                "Granite returned an empty response.  "
                "The prompt may be too long or the model context window exceeded."
            )

        return generated_text
