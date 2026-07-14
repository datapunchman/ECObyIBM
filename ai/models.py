"""
ai.models
=========
Pydantic models for every input and output of the AI Reasoning Engine.

Design notes
------------
* All request/response models are ``frozen=True`` — safe to cache and pass
  across async contexts.
* ``AnalysisRequest`` is the single entry-point for the ``analyze_change``
  pipeline.
* ``PromptPackage`` is the fully-assembled artefact returned to the caller
  before an LLM is invoked — it carries the prompt text alongside all the
  structured metadata snapshots that were used to build it, so downstream
  phases can attach the LLM response without re-fetching metadata.
* ``ImpactAnalysisResponse`` is the LLM output schema validated by Pydantic
  after the Granite response is received.
* ``AnalysisResult`` is the top-level API response that combines the prompt
  package with the parsed LLM impact analysis.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class RiskLevel(str, Enum):
    """Severity classification for a proposed change."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ChangeType(str, Enum):
    """Broad category of the requested change."""

    SCHEMA = "schema"          # Add / rename / drop a table or column
    MEASURE = "measure"        # Add / modify / remove a DAX measure
    RELATIONSHIP = "relationship"  # Add / change / remove a relationship
    REPORT = "report"          # Add / change / remove a report visual or page
    DATA = "data"              # Source data change (pipeline, ETL, Gold table)
    UNKNOWN = "unknown"


# ---------------------------------------------------------------------------
# Inbound request
# ---------------------------------------------------------------------------


class AnalysisRequest(BaseModel):
    """Describes the change a user or orchestrator wants to analyse.

    Parameters
    ----------
    request:
        Free-text description of the proposed change.  This is the primary
        input to the prompt builder.
    change_type:
        Optional hint about the category of change.  When omitted the
        prompt builder infers it from the request text.
    context:
        Optional bag of extra context key/value pairs — e.g. Jira ticket,
        sprint, author, target environment.
    """

    model_config = {"frozen": True}

    request: str = Field(
        ...,
        min_length=10,
        description="Plain-English description of the proposed change.",
        examples=["Rename the Revenue column in sales_dashboard to GrossRevenue"],
    )
    change_type: ChangeType = Field(
        ChangeType.UNKNOWN,
        description="Optional category hint for the change.",
    )
    context: Dict[str, str] = Field(
        default_factory=dict,
        description="Arbitrary key/value metadata passed through to the prompt.",
    )


# ---------------------------------------------------------------------------
# Metadata snapshot (trimmed for prompt construction)
# ---------------------------------------------------------------------------


class MetadataSnapshot(BaseModel):
    """Compact, prompt-ready view of the Metadata Engine payload.

    This is *not* the full MetadataPayload — it omits annotation blobs and
    large expression strings to keep the prompt within token budgets.
    """

    model_config = {"frozen": False}  # mutable so the client can populate it

    tables: List[Dict[str, Any]] = Field(default_factory=list)
    columns: List[Dict[str, Any]] = Field(default_factory=list)
    measures: List[Dict[str, Any]] = Field(default_factory=list)
    relationships: List[Dict[str, Any]] = Field(default_factory=list)
    reports: List[Dict[str, Any]] = Field(default_factory=list)
    dependency_edge_count: int = Field(
        0, description="Total dependency edges (informational)."
    )

    @classmethod
    def from_payload(cls, payload: Dict[str, Any]) -> "MetadataSnapshot":
        """Build a trimmed snapshot from the raw Metadata API JSON response."""
        return cls(
            tables=_trim_tables(payload.get("tables", [])),
            columns=_trim_columns(payload.get("columns", [])),
            measures=_trim_measures(payload.get("measures", [])),
            relationships=_trim_relationships(payload.get("relationships", [])),
            reports=_trim_reports(payload.get("reports", [])),
            dependency_edge_count=len(payload.get("dependencies", [])),
        )


# ---------------------------------------------------------------------------
# Prompt package
# ---------------------------------------------------------------------------


class PromptSection(BaseModel):
    """A labelled section within the assembled prompt."""

    model_config = {"frozen": True}

    heading: str = Field(..., description="Section heading displayed in the prompt.")
    content: str = Field(..., description="Body text for this section.")


class PromptPackage(BaseModel):
    """The fully-assembled prompt, ready to be sent to an LLM.

    Also carries the structured data used to build it so the LLM integration
    phase can attach the response without re-fetching metadata.
    """

    model_config = {"frozen": True}

    request: AnalysisRequest = Field(..., description="Original change request.")
    metadata_snapshot: MetadataSnapshot = Field(
        ..., description="Trimmed metadata used to build the prompt."
    )
    prompt_text: str = Field(
        ..., description="Complete prompt string ready for an LLM call."
    )
    sections: List[PromptSection] = Field(
        default_factory=list,
        description="Individual sections for structured introspection.",
    )
    token_estimate: int = Field(
        0,
        description="Rough token estimate (word count × 1.3) for budget checks.",
    )


# ---------------------------------------------------------------------------
# Expected LLM output schema (for next-phase parsing)
# ---------------------------------------------------------------------------


class DeploymentStep(BaseModel):
    """A single step in a deployment plan."""

    model_config = {"frozen": True}

    order: int
    action: str
    owner: Optional[str] = None
    notes: Optional[str] = None


class ValidationCheck(BaseModel):
    """A single item in a post-deployment validation checklist."""

    model_config = {"frozen": True}

    check: str
    expected_outcome: str
    tool: Optional[str] = None


class ImpactAnalysisResponse(BaseModel):
    """Schema of the JSON object the LLM is instructed to return.

    ``deployment_plan``, ``validation_checklist``, and ``rollback_plan`` are
    typed as ``List[str]``.  The parser normalises any plain-string variants
    returned by the model into single-element lists before construction.
    """

    model_config = {"frozen": True}

    executive_summary: str = Field(
        ..., description="One-paragraph plain-English summary for non-technical stakeholders."
    )
    risk_level: RiskLevel = Field(..., description="Assessed risk of the change.")
    risk_rationale: str = Field(..., description="Justification for the assigned risk level.")
    affected_tables: List[str] = Field(default_factory=list)
    affected_columns: List[str] = Field(default_factory=list)
    affected_measures: List[str] = Field(default_factory=list)
    affected_reports: List[str] = Field(default_factory=list)
    impact_analysis: str = Field(
        ..., description="Detailed analysis of downstream impacts across all artifact types."
    )
    deployment_plan: List[str] = Field(
        default_factory=list,
        description="Ordered deployment steps.",
    )
    validation_checklist: List[str] = Field(
        default_factory=list,
        description="Post-deployment checks.",
    )
    rollback_plan: List[str] = Field(
        default_factory=list,
        description="Step-by-step instructions to revert the change if validation fails.",
    )
    dependencies_impacted: int = Field(
        0,
        description="Count of dependency graph edges affected by this change.",
    )


# ---------------------------------------------------------------------------
# Combined API result
# ---------------------------------------------------------------------------


class AnalysisResult(BaseModel):
    """Top-level response returned by ``POST /analyze``.

    Combines the prompt package (inputs + assembled prompt) with the
    structured impact analysis produced by Granite.
    """

    model_config = {"frozen": True}

    request: AnalysisRequest = Field(..., description="The original change request.")
    prompt_text: str = Field(..., description="The prompt that was sent to Granite.")
    token_estimate: int = Field(0, description="Estimated prompt token count.")
    impact_analysis: ImpactAnalysisResponse = Field(
        ..., description="Structured impact analysis returned by Granite."
    )
    model_id: str = Field(..., description="Granite model ID that produced the response.")
    parse_success: bool = Field(
        True,
        description="False when the LLM response could not be fully parsed.",
    )


# ---------------------------------------------------------------------------
# Internal trim helpers (keep models.py self-contained)
# ---------------------------------------------------------------------------


def _trim_tables(tables: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    keep = {"name", "source_type", "is_hidden", "is_date_table", "description"}
    return [{k: v for k, v in t.items() if k in keep} for t in tables]


def _trim_columns(columns: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    keep = {"table_name", "name", "data_type", "is_hidden", "is_key",
            "display_folder", "data_category", "description"}
    return [{k: v for k, v in c.items() if k in keep} for c in columns]


def _trim_measures(measures: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    keep = {"table_name", "name", "expression", "display_folder",
            "referenced_tables", "referenced_columns", "referenced_measures", "description"}
    return [{k: v for k, v in m.items() if k in keep} for m in measures]


def _trim_relationships(rels: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    keep = {"relationship_id", "from_table", "from_column",
            "to_table", "to_column", "is_active", "cross_filter_behavior"}
    return [{k: v for k, v in r.items() if k in keep} for r in rels]


def _trim_reports(reports: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    keep = {"page_name", "display_name", "used_measures", "used_columns", "used_tables"}
    return [{k: v for k, v in r.items() if k in keep} for r in reports]
