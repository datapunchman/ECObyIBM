"""
ai
==
AI Reasoning Engine for the Enterprise Change Orchestrator.

Public API
----------
    from ai import ChangeAnalyzer, AnalysisRequest

    # Full pipeline (calls IBM Granite)
    analyzer = ChangeAnalyzer()
    result   = analyzer.analyze_change(
        "Rename the Revenue column in sales_dashboard to GrossRevenue"
    )
    print(result.impact_analysis.risk_level)
    print(result.impact_analysis.affected_tables)

    # Prompt-only (no Granite call — for testing / preview)
    package = analyzer.build_prompt_only(
        "Add a new NetRevenue column to sales_dashboard"
    )
    print(package.prompt_text)

Modules
-------
ai.models          — Pydantic request / response / result data models
ai.client          — Metadata API HTTP client
ai.granite_client  — IBM watsonx.ai Granite SDK wrapper with retry
ai.response_parser — Extract + validate ImpactAnalysisResponse from raw LLM text
ai.prompt_builder  — Structured LLM prompt construction
ai.analyzer        — Orchestrator (full analyze_change pipeline)
ai.engine          — FastAPI HTTP application
"""

from ai.analyzer import ChangeAnalyzer
from ai.client import MetadataClient, MetadataClientError
from ai.granite_client import GraniteClient, GraniteClientError, GraniteConfig
from ai.models import (
    AnalysisRequest,
    AnalysisResult,
    ChangeType,
    DeploymentStep,
    ImpactAnalysisResponse,
    MetadataSnapshot,
    PromptPackage,
    PromptSection,
    RiskLevel,
    ValidationCheck,
)
from ai.prompt_builder import PromptBuilder
from ai.response_parser import ParseFailureResponse, ResponseParser

__all__ = [
    "ChangeAnalyzer",
    "MetadataClient",
    "MetadataClientError",
    "GraniteClient",
    "GraniteClientError",
    "GraniteConfig",
    "PromptBuilder",
    "ResponseParser",
    "ParseFailureResponse",
    "AnalysisRequest",
    "AnalysisResult",
    "ChangeType",
    "DeploymentStep",
    "ImpactAnalysisResponse",
    "MetadataSnapshot",
    "PromptPackage",
    "PromptSection",
    "RiskLevel",
    "ValidationCheck",
]
