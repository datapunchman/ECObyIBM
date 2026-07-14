"""
ai.engine
=========
FastAPI application for the AI Reasoning Engine.

Endpoints
---------
POST /analyze
    Accept an ``AnalysisRequest``, run the full pipeline
    (metadata → prompt → IBM Granite → parsed impact analysis), and return
    a structured ``AnalysisResult`` JSON.

POST /analyze/raw
    Same as ``/analyze`` but also embeds the unfiltered metadata payload
    for debugging.

GET  /analyze/preview
    [UNCHANGED] Returns a ``PromptPackage`` for the built-in example
    request — no Granite call, no IBM credentials required.

GET  /analyze/health
    Liveness + readiness: checks both the Metadata API and IBM watsonx.ai.

GET  /analyze/reload
    Clears the cached analyzer and forces a fresh construction on the next
    request (useful after credential rotation).

Configuration
-------------
Required environment variables:

    IBM_API_KEY        — watsonx.ai IAM API key
    IBM_PROJECT_ID     — watsonx.ai project ID
    IBM_URL            — watsonx.ai endpoint (e.g. https://us-south.ml.cloud.ibm.com)

Optional environment variables (with defaults):

    METADATA_API_URL   — Metadata Engine base URL  (default: http://127.0.0.1:8000)
    IBM_MODEL_ID       — Granite model ID           (default: ibm/granite-4-h-small)
    IBM_MAX_TOKENS     — Max new tokens             (default: 4096)
    IBM_TEMPERATURE    — Sampling temperature       (default: 0.1)
    IBM_TIMEOUT        — Per-call timeout (seconds) (default: 120)
    IBM_MAX_RETRIES    — Retry attempts             (default: 3)

Run
---
    # Copy and populate credentials first:
    cp .env.example .env

    uvicorn ai.engine:app --reload --host 127.0.0.1 --port 8001
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache
from typing import Any, Dict

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # python-dotenv not installed — rely on shell environment

from fastapi import FastAPI, HTTPException, status
from fastapi.responses import JSONResponse

from ai.analyzer import ChangeAnalyzer
from ai.client import MetadataClientError
from ai.granite_client import GraniteClientError
from ai.models import AnalysisRequest, AnalysisResult, ChangeType

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Enterprise Change Orchestrator — AI Reasoning Engine",
    description=(
        "Accepts a business change request, fetches the current metadata "
        "snapshot, sends a structured prompt to IBM Granite, and returns a "
        "parsed impact analysis with risk level, affected artifacts, deployment "
        "plan, validation checklist, and rollback instructions."
    ),
    version="2.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# ---------------------------------------------------------------------------
# Shared analyzer (one instance per worker process)
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def _get_analyzer() -> ChangeAnalyzer:
    """Build and cache the ChangeAnalyzer for the process lifetime.

    The GraniteClient inside is created lazily on first use of
    ``/analyze``, so this call never fails even if IBM credentials are
    not yet set — the error surfaces only when an LLM call is actually made.
    """
    return ChangeAnalyzer()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/analyze/health", summary="Liveness and readiness check")
def health() -> Dict[str, Any]:
    """Return health status for this service and its upstream dependencies.

    Checks:
    * Metadata Engine reachability.
    * IBM watsonx.ai credential presence (does *not* make a live API call).
    """
    analyzer = _get_analyzer()
    metadata_ok = analyzer.ready()

    ibm_vars = {
        "IBM_API_KEY": bool(os.environ.get("IBM_API_KEY")),
        "IBM_PROJECT_ID": bool(os.environ.get("IBM_PROJECT_ID")),
        "IBM_URL": bool(os.environ.get("IBM_URL")),
    }
    ibm_configured = all(ibm_vars.values())

    overall = "ok" if (metadata_ok and ibm_configured) else "degraded"
    return {
        "status": overall,
        "metadata_api": "healthy" if metadata_ok else "unreachable",
        "ibm_credentials": "configured" if ibm_configured else "missing",
        "ibm_vars": ibm_vars,
        "model_id": os.environ.get("IBM_MODEL_ID", "ibm/granite-4-h-small"),
        "service": "ai-reasoning-engine",
    }


@app.post(
    "/analyze",
    summary="Run full impact analysis via IBM Granite",
    response_description=(
        "AnalysisResult: structured impact analysis including risk level, "
        "affected artifacts, deployment plan, and rollback instructions."
    ),
    status_code=status.HTTP_200_OK,
)
def analyze(request: AnalysisRequest) -> JSONResponse:
    """Run the full pipeline for the given change request.

    1. Fetches the current metadata snapshot from the Metadata Engine.
    2. Assembles a relevance-filtered prompt from the metadata.
    3. Calls IBM Granite via the watsonx.ai chat REST API.
    4. Parses and validates the JSON response into an ``ImpactAnalysisResponse``.
    5. Returns an ``AnalysisResult`` containing the full structured output.

    If Granite's response cannot be parsed, a ``parse_success=false`` result
    is still returned (HTTP 200) with the raw error embedded in
    ``impact_analysis.executive_summary``.
    """
    analyzer = _get_analyzer()
    try:
        result: AnalysisResult = analyzer.analyze_change(request)
    except EnvironmentError as exc:
        logger.error("IBM credential error: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "IBM watsonx.ai credentials are not configured. "
                f"Set IBM_API_KEY, IBM_PROJECT_ID, and IBM_URL. Detail: {exc}"
            ),
        ) from exc
    except MetadataClientError as exc:
        logger.error("Metadata API error: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Metadata API unavailable: {exc}",
        ) from exc
    except GraniteClientError as exc:
        logger.error("Granite API error: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"IBM Granite call failed: {exc}",
        ) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("Unexpected error during analysis")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Internal error: {exc}",
        ) from exc

    return JSONResponse(
        content=_result_to_dict(result),
        status_code=status.HTTP_200_OK,
    )


@app.post(
    "/analyze/raw",
    summary="Run full analysis and include unfiltered metadata payload",
    status_code=status.HTTP_200_OK,
)
def analyze_raw(request: AnalysisRequest) -> JSONResponse:
    """Same as ``POST /analyze`` but also embeds the raw unfiltered
    metadata payload in the ``_raw_metadata`` key for debugging."""
    analyzer = _get_analyzer()
    try:
        raw_payload = analyzer._metadata_client.fetch_raw()
        from ai.models import MetadataSnapshot  # avoid circular import at module level
        snapshot = MetadataSnapshot.from_payload(raw_payload)
        result_or_pkg = analyzer.analyze_from_snapshot(request, snapshot, call_llm=True)
    except EnvironmentError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"IBM credentials not configured: {exc}",
        ) from exc
    except MetadataClientError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Metadata API unavailable: {exc}",
        ) from exc
    except GraniteClientError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Granite call failed: {exc}",
        ) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("Unexpected error during raw analysis")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Internal error: {exc}",
        ) from exc

    output = _result_to_dict(result_or_pkg)
    output["_raw_metadata"] = raw_payload
    return JSONResponse(content=output, status_code=status.HTTP_200_OK)


@app.get(
    "/analyze/preview",
    summary="[UNCHANGED] Dry-run prompt preview — no Granite call, no IBM credentials needed",
)
def preview() -> JSONResponse:
    """Return a ``PromptPackage`` assembled from a built-in example change
    request.  Does **not** call Granite or require IBM credentials — safe
    for UI integration testing and CI/CD pipeline validation."""
    example = AnalysisRequest(
        request=(
            "Rename the 'Revenue' column in the sales_dashboard table to "
            "'GrossRevenue' to align with the new enterprise naming standard."
        ),
        change_type=ChangeType.SCHEMA,
        context={"ticket": "DATA-1042", "author": "data-engineering-team"},
    )
    analyzer = _get_analyzer()
    try:
        package = analyzer.build_prompt_only(example)
    except MetadataClientError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Metadata API unavailable: {exc}",
        ) from exc

    return JSONResponse(
        content=_prompt_package_to_dict(package),
        status_code=status.HTTP_200_OK,
    )


@app.get("/analyze/reload", summary="Invalidate cached analyzer")
def reload_analyzer() -> Dict[str, str]:
    """Clear the LRU cache and force a fresh ``ChangeAnalyzer`` construction
    on the next request.  Useful after credential rotation or config changes."""
    _get_analyzer.cache_clear()
    return {"status": "reloaded", "message": "ChangeAnalyzer cache cleared."}


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------


def _result_to_dict(result: Any) -> Dict[str, Any]:
    """Serialise an AnalysisResult or PromptPackage to a plain dict."""
    if isinstance(result, AnalysisResult):
        return {
            "request": result.request.model_dump(),
            "token_estimate": result.token_estimate,
            "model_id": result.model_id,
            "parse_success": result.parse_success,
            "impact_analysis": result.impact_analysis.model_dump(),
        }
    # Fallback for PromptPackage (analyze_from_snapshot with call_llm=False)
    return _prompt_package_to_dict(result)


def _prompt_package_to_dict(package: Any) -> Dict[str, Any]:
    """Serialise a PromptPackage to a plain dict."""
    return {
        "request": package.request.model_dump(),
        "token_estimate": package.token_estimate,
        "sections": [
            {"heading": s.heading, "content": s.content}
            for s in package.sections
        ],
        "prompt_text": package.prompt_text,
        "metadata_snapshot": {
            "tables": package.metadata_snapshot.tables,
            "columns": package.metadata_snapshot.columns,
            "measures": package.metadata_snapshot.measures,
            "relationships": package.metadata_snapshot.relationships,
            "reports": package.metadata_snapshot.reports,
            "dependency_edge_count": package.metadata_snapshot.dependency_edge_count,
        },
    }
