"""
ai.analyzer
===========
``ChangeAnalyzer`` — orchestrates the full impact-analysis pipeline.

v1 pipeline (legacy / ``/analyze``)
-------------------------------------

    ChangeAnalyzer.analyze_change(request)
        │
        ├─ 1. Resolve / validate the AnalysisRequest
        ├─ 2. MetadataClient.fetch_snapshot()        → MetadataSnapshot
        ├─ 3. PromptBuilder.build(request, snapshot)  → PromptPackage
        ├─ 4. GraniteClient.generate(prompt_text)    → raw_text (str)
        ├─ 5. ResponseParser.parse(raw_text)          → ImpactAnalysisResponse
        └─ 6. Return AnalysisResult

v2 pipeline (graph-grounded / ``/analyze/v2``)
------------------------------------------------

    ChangeAnalyzer.analyze_change_v2(request)
        │
        ├─ 1. Resolve / validate the AnalysisRequest
        ├─ 2. MetadataClient.fetch_enterprise_graph()  → EnterpriseGraph
        ├─ 3. EnterpriseChangeAnalyzer.analyze(text)  → EnterpriseChangeAnalysis
        ├─ 4. GraphOrchestrator.orchestrate(analysis) → EnterpriseGraphResult
        ├─ 5. PromptBuilder.build_from_graph(req, gr) → prompt_text (str)
        ├─ 6. GraniteClient.generate(prompt_text)     → raw_text (str)
        ├─ 7. ResponseParser.parse_v2(raw_text)        → llm_summary dict
        └─ 8. Return V2AnalysisResult

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

    # v1 pipeline (calls Granite, old response shape)
    analyzer = ChangeAnalyzer()
    result = analyzer.analyze_change(
        "Rename the Revenue column in sales_dashboard to GrossRevenue"
    )
    print(result.impact_analysis.risk_level)

    # v2 pipeline (graph-grounded, new response shape)
    v2_result = analyzer.analyze_change_v2(
        "Rename the Revenue column in sales_dashboard to GrossRevenue"
    )
    print(v2_result["llm_summary"]["risk_level"])

    # Prompt-only (no Granite call)
    package = analyzer.build_prompt_only(
        "Add a new NetRevenue column to sales_dashboard"
    )
    print(package.prompt_text)
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Union

from ai.client import MetadataClient
from ai.granite_client import GraniteClient, GraniteConfig
from ai.graph_orchestrator import GraphOrchestrator
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
from change.analyzer import EnterpriseChangeAnalyzer

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
        graph_orchestrator: Optional[GraphOrchestrator] = None,
    ) -> None:
        self._metadata_client: MetadataClient = metadata_client or MetadataClient()
        self._prompt_builder: PromptBuilder = prompt_builder or PromptBuilder()
        self._parser: ResponseParser = ResponseParser()
        # GraphOrchestrator is stateless — safe to share across calls.
        self._graph_orchestrator: GraphOrchestrator = (
            graph_orchestrator or GraphOrchestrator()
        )
        # NOTE: EnterpriseChangeAnalyzer requires an EnterpriseGraph at
        # construction and is therefore built fresh per v2 call (the graph is
        # fetched from the Metadata API at call time, not at startup).

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

    def analyze_change_v2(
        self,
        request: Union[str, AnalysisRequest],
        change_type: ChangeType = ChangeType.UNKNOWN,
    ) -> Dict[str, Any]:
        """Run the graph-grounded v2 pipeline.

        Steps
        -----
        1. Parse ``request`` into an ``AnalysisRequest``.
        2. Fetch the ``EnterpriseGraph`` from the Metadata Engine.
        3. Run ``EnterpriseChangeAnalyzer`` for deterministic BFS traversal.
        4. Run ``GraphOrchestrator`` to produce the 19-bucket impact result.
        5. Build a graph-grounded prompt via ``PromptBuilder.build_from_graph()``.
        6. Call Granite for *reasoning only* (no dependency discovery).
        7. Parse the Granite response and merge with graph data.
        8. Return the new V2 response shape dict.

        Parameters
        ----------
        request:
            Plain-English change description or a full ``AnalysisRequest``.
        change_type:
            Optional category hint.

        Returns
        -------
        dict
            New V2 response shape containing ``change_request``,
            ``source_asset``, ``graph_analysis``, and ``llm_summary``.

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
            "analyze_change_v2: '%s…' [type=%s]",
            analysis_request.request[:80],
            analysis_request.change_type.value,
        )

        # Step 1 — fetch enterprise graph from Metadata Engine
        enterprise_graph = self._metadata_client.fetch_enterprise_graph()

        # Step 2 — build a fresh EnterpriseChangeAnalyzer with the live graph
        #           (it requires the graph at construction, not as a method arg)
        change_analyzer = EnterpriseChangeAnalyzer(graph=enterprise_graph)
        change_analysis = change_analyzer.analyze(analysis_request.request)

        # Step 3 — map into 19 enterprise buckets with provenance
        graph_result = self._graph_orchestrator.orchestrate(change_analysis)

        # Step 4 — graph-grounded prompt (Granite reasons, never discovers)
        prompt_text = self._prompt_builder.build_from_graph(
            analysis_request, graph_result
        )

        # Step 5 — Granite reasoning call
        raw_text = self._call_granite(prompt_text)

        # Step 6 — collect empty buckets for the hallucination scrubber
        empty_buckets: set = {
            bucket
            for bucket, assets in graph_result.graph_analysis.items()
            if bucket not in ("dependency_paths", "metrics") and not assets
        }

        # Step 7 — parse Granite's llm_summary fields; scrub absent-system mentions
        llm_summary = self._parser.parse_v2(raw_text, empty_buckets=empty_buckets)

        # Step 8 — assemble final v2 response dict
        source_asset = graph_result.source_asset
        result: Dict[str, Any] = {
            "change_request": {
                "original_request": change_analysis.change_request.original_request,
                "change_type": change_analysis.change_request.change_type.value,
                "target_name": change_analysis.change_request.target_name,
                "new_name": change_analysis.change_request.new_name,
                "table_name": change_analysis.change_request.table_name,
            },
            "source_asset": {
                "id": source_asset.id if source_asset else None,
                "name": source_asset.name if source_asset else None,
                "type": source_asset.asset_type.value if source_asset else None,
                "system": source_asset.system.value if source_asset else None,
            },
            "graph_analysis": _serialise_graph_analysis(graph_result.graph_analysis),
            "llm_summary": llm_summary,
        }

        logger.info(
            "analyze_change_v2 complete: %d assets, risk=%s",
            graph_result.metrics.get("total_assets", 0),
            llm_summary.get("risk_level", "unknown"),
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


# ---------------------------------------------------------------------------
# Module-level serialisation helpers
# ---------------------------------------------------------------------------


def _serialise_graph_analysis(graph_analysis: Dict[str, Any]) -> Dict[str, Any]:
    """Convert an ``EnterpriseGraphResult.graph_analysis`` dict to JSON-safe form.

    Replaces every :class:`~ai.graph_orchestrator.ImpactedAsset` with its
    ``to_dict()`` representation so the result is directly JSON-serialisable.

    Parameters
    ----------
    graph_analysis:
        The ``graph_analysis`` dict from an ``EnterpriseGraphResult``, which
        maps bucket names → ``list[ImpactedAsset]`` plus the special
        ``dependency_paths`` and ``metrics`` keys.

    Returns
    -------
    dict
        JSON-safe copy of *graph_analysis*.
    """
    out: Dict[str, Any] = {}
    for key, value in graph_analysis.items():
        if key == "metrics":
            out[key] = value  # already a plain dict
        elif key == "dependency_paths":
            out[key] = value  # list[list[str]]
        elif isinstance(value, list):
            out[key] = [
                item.to_dict() if hasattr(item, "to_dict") else item
                for item in value
            ]
        else:
            out[key] = value
    return out
