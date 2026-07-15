"""
ai.client
=========
HTTP client for the Metadata Engine API.

The ``MetadataClient`` calls the running Metadata Engine (``metadata.engine``)
and returns a typed ``MetadataSnapshot`` ready for prompt construction.

Configuration
-------------
The base URL is resolved from the environment variable ``METADATA_API_URL``
(default: ``http://127.0.0.1:8000``).

    export METADATA_API_URL=http://127.0.0.1:8000

Usage
-----
    from ai.client import MetadataClient

    client = MetadataClient()
    snapshot = client.fetch_snapshot()
    graph   = client.fetch_enterprise_graph()

Error handling
--------------
* ``MetadataClientError`` is raised for all unrecoverable HTTP or network
  errors, wrapping the original exception as ``__cause__``.
* Transient timeouts are retried up to ``max_retries`` times with exponential
  back-off before raising.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, Optional

import requests
from requests import Response, Session
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from ai.models import MetadataSnapshot
from graph.adapter import MetadataAdapter
from graph.enterprise_graph import EnterpriseGraph

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Custom exception
# ---------------------------------------------------------------------------


class MetadataClientError(RuntimeError):
    """Raised when the Metadata API cannot be reached or returns an error."""

    def __init__(self, message: str, status_code: Optional[int] = None) -> None:
        super().__init__(message)
        self.status_code = status_code


# ---------------------------------------------------------------------------
# Retry strategy
# ---------------------------------------------------------------------------

_DEFAULT_RETRY = Retry(
    total=3,
    backoff_factor=0.5,          # 0.5 s, 1 s, 2 s
    status_forcelist={502, 503, 504},
    allowed_methods={"GET"},
    raise_on_status=False,
)


def _build_session(retry: Retry = _DEFAULT_RETRY) -> Session:
    """Build a ``requests.Session`` with the retry adapter mounted."""
    session = Session()
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


# ---------------------------------------------------------------------------
# MetadataClient
# ---------------------------------------------------------------------------


class MetadataClient:
    """Thin HTTP client for the Metadata Engine REST API.

    Parameters
    ----------
    base_url:
        Root URL of the running Metadata Engine.  Resolved from the
        ``METADATA_API_URL`` environment variable when omitted.
    timeout:
        Per-request timeout in seconds (connect + read).
    session:
        Optional pre-built ``requests.Session``.  Primarily used for
        testing (pass a mock session to avoid real HTTP calls).
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        timeout: float = 30.0,
        session: Optional[Session] = None,
    ) -> None:
        self.base_url: str = (
            base_url
            or os.getenv("METADATA_API_URL", "http://127.0.0.1:8000")
        ).rstrip("/")
        self.timeout = timeout
        self._session: Session = session or _build_session()
        logger.debug("MetadataClient initialised → %s", self.base_url)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def fetch_snapshot(self) -> MetadataSnapshot:
        """Fetch the full metadata payload and return a ``MetadataSnapshot``.

        Returns
        -------
        MetadataSnapshot
            Trimmed, prompt-ready view of the semantic model and report pages.

        Raises
        ------
        MetadataClientError
            On any HTTP error, network failure, or non-200 response.
        """
        raw = self._get("/metadata")
        snapshot = MetadataSnapshot.from_payload(raw)
        logger.info(
            "Metadata snapshot fetched: %d tables, %d columns, %d measures, "
            "%d relationships, %d pages, %d dependency edges",
            len(snapshot.tables),
            len(snapshot.columns),
            len(snapshot.measures),
            len(snapshot.relationships),
            len(snapshot.reports),
            snapshot.dependency_edge_count,
        )
        return snapshot

    def fetch_raw(self) -> Dict[str, Any]:
        """Return the raw Metadata API payload as a plain dict.

        Useful when callers need the unfiltered full payload (e.g. for
        serialisation to a file or for testing).
        """
        return self._get("/metadata")

    def fetch_enterprise_graph(self) -> EnterpriseGraph:
        """Fetch the full metadata payload and return an ``EnterpriseGraph``.

        The graph is built by :class:`~graph.adapter.MetadataAdapter` from the
        raw Metadata API payload.  The result is NOT cached — each call fetches
        fresh data.

        Returns
        -------
        EnterpriseGraph
            Fully populated graph ready for
            :class:`~change.analyzer.EnterpriseChangeAnalyzer`.

        Raises
        ------
        MetadataClientError
            On any HTTP error, network failure, or non-200 response.
        """
        raw = self._get("/metadata")
        graph = MetadataAdapter.to_enterprise_graph(raw)
        logger.info(
            "EnterpriseGraph fetched: %d assets, %d relationships",
            len(graph.assets),
            len(graph.relationships),
        )
        return graph

    def health_check(self) -> bool:
        """Return ``True`` if the Metadata API is reachable and healthy.

        Does **not** raise — intended for use in startup guards.
        """
        try:
            resp = self._session.get(
                f"{self.base_url}/health", timeout=self.timeout
            )
            return resp.status_code == 200
        except requests.RequestException as exc:
            logger.warning("Metadata API health check failed: %s", exc)
            return False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get(self, path: str) -> Dict[str, Any]:
        """Issue a GET request and return the parsed JSON body.

        Parameters
        ----------
        path:
            URL path relative to ``base_url`` (must start with ``/``).

        Returns
        -------
        dict
            Parsed JSON response body.

        Raises
        ------
        MetadataClientError
            On connection failure, timeout, or non-200 HTTP status.
        """
        url = f"{self.base_url}{path}"
        logger.debug("GET %s", url)
        start = time.monotonic()

        try:
            response: Response = self._session.get(url, timeout=self.timeout)
        except requests.ConnectionError as exc:
            raise MetadataClientError(
                f"Cannot connect to Metadata API at {url}: {exc}"
            ) from exc
        except requests.Timeout as exc:
            raise MetadataClientError(
                f"Timeout waiting for Metadata API at {url} after {self.timeout}s"
            ) from exc
        except requests.RequestException as exc:
            raise MetadataClientError(
                f"Unexpected error calling Metadata API: {exc}"
            ) from exc

        elapsed = (time.monotonic() - start) * 1000
        logger.debug("GET %s → %d (%.0f ms)", url, response.status_code, elapsed)

        if response.status_code != 200:
            raise MetadataClientError(
                f"Metadata API returned HTTP {response.status_code} for {url}: "
                f"{response.text[:200]}",
                status_code=response.status_code,
            )

        try:
            return response.json()
        except ValueError as exc:
            raise MetadataClientError(
                f"Metadata API returned non-JSON body from {url}"
            ) from exc
