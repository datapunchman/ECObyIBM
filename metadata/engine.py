"""
metadata.engine
===============
FastAPI application exposing the Metadata Engine over HTTP.

Endpoints
---------
GET  /metadata
    Returns the full MetadataPayload as JSON.

GET  /metadata/tables
    Returns only the tables list.

GET  /metadata/columns
    Returns only the columns list.

GET  /metadata/measures
    Returns only the measures list.

GET  /metadata/relationships
    Returns only the relationships list.

GET  /metadata/reports
    Returns only the report pages list.

GET  /metadata/dependencies
    Returns only the dependency graph edges.

GET  /metadata/dependencies/{artifact_type}/{artifact_name}
    Returns all dependency edges where ``source_name`` or ``target_name``
    matches the given artifact.

GET  /health
    Liveness check.

Configuration
-------------
The paths to the semantic model and report are resolved from environment
variables:

    SEMANTIC_MODEL_PATH   (default: "sales.SemanticModel")
    REPORT_PATH           (default: "sales.Report")

Run
---
    uvicorn metadata.engine:app --reload --host 127.0.0.1 --port 8000
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache
from typing import Any, Dict, List

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # python-dotenv not installed — rely on shell environment

from fastapi import FastAPI, HTTPException, Path
from fastapi.responses import JSONResponse

from metadata.loader import MetadataEngine
from metadata.models import MetadataPayload

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
    title="Enterprise Change Orchestrator — Metadata Engine",
    description=(
        "Exposes metadata extracted from the Power BI Semantic Model (TMDL) "
        "and PBIR report definitions, together with the inferred dependency graph."
    ),
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# ---------------------------------------------------------------------------
# Startup — eager-load the metadata once, cache it
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def _get_payload() -> MetadataPayload:
    """Load and cache the metadata payload on first access."""
    semantic_model_path = os.getenv("SEMANTIC_MODEL_PATH", "sales.SemanticModel")
    report_path = os.getenv("REPORT_PATH", "sales.Report")
    engine = MetadataEngine(
        semantic_model_path=semantic_model_path,
        report_path=report_path,
    )
    try:
        return engine.load()
    except FileNotFoundError as exc:
        logger.error("MetadataEngine failed to load: %s", exc)
        raise


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/health", summary="Liveness check")
def health() -> Dict[str, str]:
    """Return 200 OK when the service is alive."""
    return {"status": "ok"}


@app.get(
    "/metadata",
    summary="Full metadata snapshot",
    response_description="Complete MetadataPayload as JSON.",
)
def get_metadata() -> JSONResponse:
    """Return the full metadata payload including all artifact types and
    the dependency graph."""
    try:
        payload = _get_payload()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return JSONResponse(content=payload.to_dict())


@app.get("/metadata/tables", summary="All tables")
def get_tables() -> List[Dict[str, Any]]:
    """Return all TableMetadata objects."""
    payload = _get_payload()
    return [t.model_dump() for t in payload.tables]


@app.get("/metadata/columns", summary="All columns")
def get_columns() -> List[Dict[str, Any]]:
    """Return all ColumnMetadata objects."""
    payload = _get_payload()
    return [c.model_dump() for c in payload.columns]


@app.get("/metadata/measures", summary="All measures")
def get_measures() -> List[Dict[str, Any]]:
    """Return all MeasureMetadata objects (with inferred DAX references)."""
    payload = _get_payload()
    return [m.model_dump() for m in payload.measures]


@app.get("/metadata/relationships", summary="All relationships")
def get_relationships() -> List[Dict[str, Any]]:
    """Return all RelationshipMetadata objects."""
    payload = _get_payload()
    return [r.model_dump() for r in payload.relationships]


@app.get("/metadata/reports", summary="All report pages")
def get_reports() -> List[Dict[str, Any]]:
    """Return all ReportMetadata objects (with visual bindings)."""
    payload = _get_payload()
    return [r.model_dump() for r in payload.reports]


@app.get("/metadata/dependencies", summary="Full dependency graph")
def get_dependencies() -> List[Dict[str, Any]]:
    """Return all DependencyMetadata edges."""
    payload = _get_payload()
    return [d.model_dump() for d in payload.dependencies]


@app.get(
    "/metadata/dependencies/{artifact_type}/{artifact_name:path}",
    summary="Dependencies for a specific artifact",
)
def get_artifact_dependencies(
    artifact_type: str = Path(
        ..., description="Artifact type: 'table', 'column', 'measure', 'report', 'relationship'."
    ),
    artifact_name: str = Path(..., description="Artifact name (qualified where applicable)."),
) -> List[Dict[str, Any]]:
    """Return all dependency edges where the given artifact appears
    as either the source or the target."""
    payload = _get_payload()
    results = [
        d.model_dump()
        for d in payload.dependencies
        if (d.source_name == artifact_name and d.source_type == artifact_type)
        or (d.target_name == artifact_name and d.target_type == artifact_type)
    ]
    if not results:
        raise HTTPException(
            status_code=404,
            detail=f"No dependencies found for {artifact_type} '{artifact_name}'.",
        )
    return results


@app.get(
    "/metadata/reload",
    summary="Invalidate cache and reload metadata",
)
def reload_metadata() -> Dict[str, Any]:
    """Force a fresh reload of the metadata payload by clearing the LRU cache."""
    _get_payload.cache_clear()
    try:
        payload = _get_payload()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {
        "status": "reloaded",
        "tables": len(payload.tables),
        "columns": len(payload.columns),
        "measures": len(payload.measures),
        "relationships": len(payload.relationships),
        "reports": len(payload.reports),
        "dependencies": len(payload.dependencies),
    }
