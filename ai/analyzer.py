"""
ai.analyzer
===========
``ChangeAnalyzer`` — orchestrates the full impact-analysis pipeline.

Updated pipeline (with IBM Granite integration)
------------------------------------------------

    ChangeAnalyzer.analyze_change(request)
        │
        ├─ 1. Resolve / validate the AnalysisRequest
        ├─ 2. MetadataClient.fetch_snapshot()       → MetadataSnapshot
        ├─ 3. PromptBuilder.build(request, snapshot) → PromptPackage
        ├─ 4. GraniteClient.generate(prompt_text)   → raw_text (str)
        ├─ 5. ResponseParser.parse(raw_text)         → ImpactAnalysisResponse
        └─ 6. Return AnalysisResult

    ChangeAnalyzer.build_prompt_only(request)
        │
        └─ Steps 1–3 only — returns PromptPackage without calling Granite.
           Used by /analyze/preview (unchanged) and unit tests.

Unit-test hooks
---------------
* Inject a ``MetadataClient`` with a mock ``requests.Session``.
* Inject a ``GraniteClient`` subclass / mock — or pass
  ``granite_client=None`` to skip LLM calls in preview-only mode.
* Use ``build_prompt_only()`` to test prompt construction without any
  network calls.
* Use ``analyze_from_snapshot()`` to bypass the Metadata API.

Usage
-----
    from ai.analyzer import ChangeAnalyzer

    # Full pipeline (calls Granite)
    analyzer = ChangeAnalyzer()
    result = analyzer.analyze_change(
        "Rename the Revenue column in sales_dashboard to GrossRevenue"
    )
    print(result.impact_analysis.risk_level)

    # Prompt-only (no Granite call)
    package = analyzer.build_prompt_only(
        "Add a new NetRevenue column to sales_dashboard"
    )
    print(package.prompt_text)
"""

from __future__ import annotations

import logging
from typing import Optional, Union

from ai.client import MetadataClient
from ai.granite_client import GraniteClient, GraniteConfig
from ai.models import (
    AnalysisRequest,
    AnalysisResult,
    ChangeType,
    ImpactAnalysisResponse,
    MetadataSnapshot,
    PromptPackage,
)
from ai.prompt_builder import PromptBuilder
from ai.response_parser import ParseFailureResponse, ResponseParser

logger = logging.getLogger(__name__)


class ChangeAnalyzer:
    """Orchestrates the full impact-analysis pipeline including IBM Granite.

    Parameters
    ----------
    metadata_client:
        Optional pre-built ``MetadataClient``.  Defaults to one created from
        the ``METADATA_API_URL`` environment variable.
    granite_client:
        Optional pre-built ``GraniteClient``.  Pass ``None`` to operate in
        prompt-only mode (useful for ``/analyze/preview`` and unit tests that
        do not have IBM credentials available).
    prompt_builder:
        Optional pre-built ``PromptBuilder``.
    granite_config:
        Optional ``GraniteConfig`` used to build a default ``GraniteClient``
        when ``granite_client`` is not supplied.
    """

    def __init__(
        self,
        metadata_client: Optional[MetadataClient] = None,
        granite_client: Optional[GraniteClient] = None,
        prompt_builder: Optional[PromptBuilder] = None,
        granite_config: Optional[GraniteConfig] = None,
    ) -> None:
        self._metadata_client: MetadataClient = metadata_client or MetadataClient()
        self._prompt_builder: PromptBuilder = prompt_builder or PromptBuilder()
        self._parser: ResponseParser = ResponseParser()

        # GraniteClient is built lazily — if credentials are not in the env,
        # it raises only when the caller actually needs LLM functionality.
        if granite_client is not None:
            self._granite: Optional[GraniteClient] = granite_client
        elif granite_config is not None:
            self._granite = GraniteClient(config=granite_config)
        else:
            # Defer construction so prompt-only / preview paths work without creds.
            self._granite = None
            self._granite_config = granite_config  # None → built from env on demand

        logger.debug(
            "ChangeAnalyzer initialised (metadata_api=%s, granite=%s)",
            self._metadata_client.base_url,
            "configured" if self._granite else "deferred",
        )

    # ------------------------------------------------------------------
    # Primary public API
    # ------------------------------------------------------------------

    def analyze_change(
        self,
        request: Union[str, AnalysisRequest],
        change_type: ChangeType = ChangeType.UNKNOWN,
    ) -> AnalysisResult:
        """Run the full pipeline: metadata → prompt → Granite → parsed result.

        Parameters
        ----------
        request:
            Plain-English change description or a full ``AnalysisRequest``.
        change_type:
            Optional category hint (ignored when ``request`` is already an
            ``AnalysisRequest``).

        Returns
        -------
        AnalysisResult
            Contains the original request, the prompt sent to Granite, the
            parsed ``ImpactAnalysisResponse``, and diagnostic flags.

        Raises
        ------
        ai.client.MetadataClientError
            If the Metadata API is unreachable.
        ai.granite_client.GraniteClientError
            If Granite is unreachable after all retries.
        EnvironmentError
            If IBM credentials are missing from the environment.
        """
        analysis_request = self._resolve_request(request, change_type)
        logger.info(
            "analyze_change: '%s…' [type=%s]",
            analysis_request.request[:80],
            analysis_request.change_type.value,
        )

        # Step 1 — metadata
        snapshot = self._fetch_snapshot()

        # Step 2 — prompt
        package = self._build_prompt(analysis_request, snapshot)

        # Step 3 — Granite
        raw_text = self._call_granite(package.prompt_text)

        # Step 4 — parse
        impact = self._parse_response(raw_text)

        parse_success = not isinstance(impact, ParseFailureResponse)
        result = AnalysisResult(
            request=analysis_request,
            prompt_text=package.prompt_text,
            token_estimate=package.token_estimate,
            impact_analysis=impact,
            model_id=self._get_granite().model_id,
            parse_success=parse_success,
        )

        logger.info(
            "analyze_change complete: risk=%s, tables=%d, measures=%d, parse_ok=%s",
            impact.risk_level.value,
            len(impact.affected_tables),
            len(impact.affected_measures),
            parse_success,
        )
        return result

    def build_prompt_only(
        self,
        request: Union[str, AnalysisRequest],
        change_type: ChangeType = ChangeType.UNKNOWN,
    ) -> PromptPackage:
        """Build and return the prompt without calling Granite.

        Used by ``/analyze/preview`` and in test scenarios where IBM
        credentials are not available.

        Parameters
        ----------
        request:
            Change description or ``AnalysisRequest``.
        change_type:
            Optional category hint.

        Returns
        -------
        PromptPackage
        """
        analysis_request = self._resolve_request(request, change_type)
        logger.info(
            "build_prompt_only: '%s…' [type=%s]",
            analysis_request.request[:80],
            analysis_request.change_type.value,
        )
        snapshot = self._fetch_snapshot()
        package = self._build_prompt(analysis_request, snapshot)
        logger.info(
            "Prompt ready: ~%d tokens, %d sections",
            package.token_estimate,
            len(package.sections),
        )
        return package

    def analyze_from_snapshot(
        self,
        request: Union[str, AnalysisRequest],
        snapshot: MetadataSnapshot,
        change_type: ChangeType = ChangeType.UNKNOWN,
        call_llm: bool = True,
    ) -> Union[AnalysisResult, PromptPackage]:
        """Run the pipeline with a pre-fetched snapshot.

        Bypasses the Metadata API HTTP call — useful for testing, batch
        processing, or when a snapshot is already in memory.

        Parameters
        ----------
        request:
            Change description or ``AnalysisRequest``.
        snapshot:
            Pre-fetched ``MetadataSnapshot``.
        change_type:
            Optional category hint.
        call_llm:
            When ``True`` (default) the Granite call is included.
            When ``False`` only the ``PromptPackage`` is returned (no LLM call).

        Returns
        -------
        AnalysisResult | PromptPackage
        """
        analysis_request = self._resolve_request(request, change_type)
        package = self._build_prompt(analysis_request, snapshot)

        if not call_llm:
            return package

        raw_text = self._call_granite(package.prompt_text)
        impact = self._parse_response(raw_text)
        parse_success = not isinstance(impact, ParseFailureResponse)

        return AnalysisResult(
            request=analysis_request,
            prompt_text=package.prompt_text,
            token_estimate=package.token_estimate,
            impact_analysis=impact,
            model_id=self._get_granite().model_id if parse_success else "unknown",
            parse_success=parse_success,
        )

    def ready(self) -> bool:
        """Return ``True`` if the Metadata API is healthy."""
        return self._metadata_client.health_check()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_request(
        request: Union[str, AnalysisRequest],
        change_type: ChangeType,
    ) -> AnalysisRequest:
        if isinstance(request, AnalysisRequest):
            return request
        return AnalysisRequest(request=request, change_type=change_type)

    def _fetch_snapshot(self) -> MetadataSnapshot:
        logger.debug(
            "Fetching metadata snapshot from %s", self._metadata_client.base_url
        )
        snapshot = self._metadata_client.fetch_snapshot()
        logger.debug(
            "Snapshot: %d tables, %d columns, %d measures, %d pages",
            len(snapshot.tables),
            len(snapshot.columns),
            len(snapshot.measures),
            len(snapshot.reports),
        )
        return snapshot

    def _build_prompt(
        self, request: AnalysisRequest, snapshot: MetadataSnapshot
    ) -> PromptPackage:
        package = self._prompt_builder.build(request, snapshot)
        logger.debug("Prompt built: %d chars", len(package.prompt_text))
        return package

    def _get_granite(self) -> GraniteClient:
        """Return the GraniteClient, building it from env if not yet created."""
        if self._granite is None:
            logger.debug("Building GraniteClient from environment credentials.")
            self._granite = GraniteClient()
        return self._granite

    def _call_granite(self, prompt_text: str) -> str:
        """Send the prompt to Granite and return raw generated text."""
        client = self._get_granite()
        logger.debug("Calling Granite (%d prompt chars)…", len(prompt_text))
        return client.generate(prompt_text)

    def _parse_response(self, raw_text: str) -> ImpactAnalysisResponse:
        """Parse the raw Granite response into a structured model."""
        return self._parser.parse(raw_text)

    # Expose model_id for AnalysisResult construction
    @property
    def _granite_model_id(self) -> str:
        if self._granite:
            return self._granite._config.model_id
        return "unknown"
