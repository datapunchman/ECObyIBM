"""
ai.graph_orchestrator
=====================
Bridges the Enterprise Graph layer and the AI reasoning layer.

Responsibilities
----------------
1. Accept an :class:`~change.models.EnterpriseChangeAnalysis` (already
   computed by the deterministic graph engine).
2. Map every impacted :class:`~graph.models.Asset` into a typed
   :class:`ImpactedAsset` with ``discovered_by="enterprise_graph"`` and
   ``confidence=1.0`` — facts, not guesses.
3. Distribute assets across the 19 enterprise buckets mandated by the API.
4. Compute deterministic metrics (total, depth, systems, critical assets)
   without calling Granite.
5. Return a fully-populated :class:`EnterpriseGraphResult` ready for the
   Granite prompt builder.

Nothing in this module calls Granite or generates text.

Architecture position
---------------------
::

    EnterpriseChangeAnalyzer   (change/analyzer.py)
            ↓
    EnterpriseChangeAnalysis   (change/models.py)
            ↓
    GraphOrchestrator          (ai/graph_orchestrator.py)   ← this file
            ↓
    EnterpriseGraphResult
            ↓
    PromptBuilder.build_from_graph()
            ↓
    GraniteClient.generate()   — reasoning only, no discovery
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from change.models import EnterpriseChangeAnalysis
from graph.models import Asset, AssetType

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# AssetType → enterprise bucket mapping
# ---------------------------------------------------------------------------
# Every AssetType value maps to exactly one API bucket key.
# Buckets not covered by the current AssetType enum default to
# "external_consumers" so new asset types never cause a KeyError.

_ASSET_TYPE_TO_BUCKET: Dict[str, str] = {
    # ── Database / SQL ─────────────────────────────────────────────────────
    AssetType.DATABASE.value:           "database_tables",
    AssetType.DATABASE_TABLE.value:     "database_tables",
    AssetType.DATABASE_COLUMN.value:    "database_tables",
    AssetType.PRIMARY_KEY.value:        "database_tables",
    AssetType.FOREIGN_KEY.value:        "database_tables",
    AssetType.TABLE.value:              "database_tables",
    AssetType.COLUMN.value:             "database_tables",
    AssetType.VIEW.value:               "views",
    AssetType.SQL_VIEW.value:           "views",
    AssetType.MATERIALIZED_VIEW.value:  "materialized_views",
    AssetType.STORED_PROCEDURE.value:   "stored_procedures",
    AssetType.FUNCTION.value:           "functions",
    AssetType.SQL_FUNCTION.value:       "functions",

    # ── Databricks / Spark ─────────────────────────────────────────────────
    AssetType.DATABRICKS_NOTEBOOK.value: "databricks_notebooks",
    AssetType.NOTEBOOK.value:            "databricks_notebooks",
    AssetType.SPARK_JOB.value:           "spark_jobs",
    AssetType.JOB.value:                 "spark_jobs",
    AssetType.DELTA_TABLE.value:         "delta_live_tables",
    AssetType.DELTA_LIVE_TABLE.value:    "delta_live_tables",
    AssetType.UNITY_CATALOG_OBJECT.value: "unity_catalog",

    # ── Storage ─────────────────────────────────────────────────────────────
    AssetType.ADLS_FILE.value:           "external_consumers",

    # ── Orchestration / Pipelines ──────────────────────────────────────────
    AssetType.PIPELINE.value:            "pipelines",
    AssetType.PIPELINE_TASK.value:       "pipelines",
    AssetType.ADF_PIPELINE.value:        "data_factory",
    AssetType.FABRIC_PIPELINE.value:     "fabric_pipelines",
    AssetType.AIRFLOW_DAG.value:         "airflow",
    AssetType.DATAFLOW.value:            "pipelines",

    # ── API ─────────────────────────────────────────────────────────────────
    AssetType.API.value:                 "apis",
    AssetType.REST_API.value:            "apis",

    # ── Power BI / BI ───────────────────────────────────────────────────────
    AssetType.SEMANTIC_MODEL.value:      "semantic_models",
    AssetType.POWERBI_DATASET.value:     "semantic_models",
    AssetType.MEASURE.value:             "semantic_models",
    AssetType.POWERBI_MEASURE.value:     "semantic_models",
    AssetType.REPORT.value:              "powerbi_reports",
    AssetType.POWERBI_REPORT.value:      "powerbi_reports",
    AssetType.VISUAL.value:              "powerbi_reports",
    AssetType.POWERBI_VISUAL.value:      "powerbi_reports",
    AssetType.DASHBOARD.value:           "dashboards",
    AssetType.POWERBI_DASHBOARD.value:   "dashboards",
}

# Complete ordered list of all 19 enterprise buckets — always present in output.
ENTERPRISE_BUCKETS: List[str] = [
    "database_tables",
    "views",
    "materialized_views",
    "stored_procedures",
    "functions",
    "databricks_notebooks",
    "spark_jobs",
    "delta_live_tables",
    "unity_catalog",
    "pipelines",
    "data_factory",
    "airflow",
    "fabric_pipelines",
    "semantic_models",
    "powerbi_reports",
    "dashboards",
    "apis",
    "external_consumers",
]

# Asset types considered "critical" — a change here has the widest blast radius
_CRITICAL_TYPES: frozenset[str] = frozenset({
    AssetType.TABLE.value,
    AssetType.DATABASE.value,
    AssetType.DATABASE_TABLE.value,
    AssetType.DELTA_TABLE.value,
    AssetType.DELTA_LIVE_TABLE.value,
    AssetType.SEMANTIC_MODEL.value,
    AssetType.POWERBI_DATASET.value,
    AssetType.PIPELINE.value,
    AssetType.ADF_PIPELINE.value,
})


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class ImpactedAsset:
    """A single downstream asset with provenance metadata.

    Every field is deterministic — no AI inference.

    Attributes
    ----------
    asset:          Original graph asset.
    type:           Asset type string (from ``AssetType`` enum).
    system:         System string (from ``SystemType`` enum).
    bucket:         Enterprise bucket this asset belongs to.
    discovered_by:  Always ``"enterprise_graph"`` — never ``"llm"``.
    confidence:     Always ``1.0`` — graph traversal is deterministic.
    """

    asset: Asset
    type: str
    system: str
    bucket: str
    discovered_by: str = "enterprise_graph"
    confidence: float = 1.0

    def to_dict(self) -> Dict:
        return {
            "id": self.asset.id,
            "asset": self.asset.name,
            "type": self.type,
            "system": self.system,
            "bucket": self.bucket,
            "discovered_by": self.discovered_by,
            "confidence": self.confidence,
        }


@dataclass
class GraphMetrics:
    """Deterministic metrics computed entirely from graph traversal.

    Attributes
    ----------
    total_assets:       Total downstream assets reachable from source.
    critical_assets:    Assets of a critical type (table, pipeline, etc.).
    max_depth:          Longest dependency path length (number of hops).
    systems_impacted:   Count of distinct SystemType values in impact set.
    buckets_impacted:   Count of non-empty enterprise buckets.
    leaf_assets:        Assets at the end of all dependency paths (no
                        further outgoing edges tracked in the graph).
    """

    total_assets: int = 0
    critical_assets: int = 0
    max_depth: int = 0
    systems_impacted: int = 0
    buckets_impacted: int = 0
    leaf_assets: int = 0

    def to_dict(self) -> Dict:
        return {
            "total_assets": self.total_assets,
            "critical_assets": self.critical_assets,
            "max_depth": self.max_depth,
            "systems_impacted": self.systems_impacted,
            "buckets_impacted": self.buckets_impacted,
            "leaf_assets": self.leaf_assets,
        }


@dataclass
class EnterpriseGraphResult:
    """Complete graph-derived impact result passed to the prompt builder.

    This is the single hand-off object between the deterministic graph
    layer and the Granite reasoning layer.

    Attributes
    ----------
    graph_analysis:     Dict of 19 enterprise buckets → list[ImpactedAsset].
                        Also includes ``dependency_paths`` and ``metrics``.
    change_analysis:    The original EnterpriseChangeAnalysis (for context).
    """

    graph_analysis: Dict
    change_analysis: EnterpriseChangeAnalysis

    @property
    def source_asset(self) -> Optional[Asset]:
        return self.change_analysis.source_asset

    @property
    def impacted_assets(self) -> List[ImpactedAsset]:
        assets = []
        for bucket in ENTERPRISE_BUCKETS:
            assets.extend(self.graph_analysis.get(bucket, []))
        return assets

    @property
    def metrics(self) -> Dict:
        return self.graph_analysis.get("metrics", {})

    @property
    def dependency_paths(self) -> List[List[str]]:
        return self.graph_analysis.get("dependency_paths", [])


# ---------------------------------------------------------------------------
# GraphOrchestrator
# ---------------------------------------------------------------------------


class GraphOrchestrator:
    """Converts an :class:`~change.models.EnterpriseChangeAnalysis` into an
    :class:`EnterpriseGraphResult` suitable for graph-grounded Granite prompts.

    This class is stateless — instantiate once, call :meth:`orchestrate`
    any number of times.
    """

    def orchestrate(
        self, change_analysis: EnterpriseChangeAnalysis
    ) -> EnterpriseGraphResult:
        """Build the full :class:`EnterpriseGraphResult` from *change_analysis*.

        Parameters
        ----------
        change_analysis:
            Fully populated result from
            :class:`~change.analyzer.EnterpriseChangeAnalyzer`.

        Returns
        -------
        EnterpriseGraphResult
            Deterministic, graph-sourced impact data.  Ready for the prompt
            builder.  No AI calls are made.
        """
        impacted_assets = self._classify_assets(change_analysis.impacted_assets)
        buckets = self._fill_buckets(impacted_assets)
        metrics = self._compute_metrics(
            impacted_assets=impacted_assets,
            paths=change_analysis.dependency_paths,
        )

        graph_analysis: Dict = {bucket: [] for bucket in ENTERPRISE_BUCKETS}
        for bucket, assets in buckets.items():
            graph_analysis[bucket] = assets

        graph_analysis["dependency_paths"] = change_analysis.dependency_paths
        graph_analysis["metrics"] = metrics.to_dict()

        logger.info(
            "GraphOrchestrator: %d assets across %d buckets, max_depth=%d",
            metrics.total_assets,
            metrics.buckets_impacted,
            metrics.max_depth,
        )

        return EnterpriseGraphResult(
            graph_analysis=graph_analysis,
            change_analysis=change_analysis,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _classify_assets(assets: List[Asset]) -> List[ImpactedAsset]:
        """Wrap every Asset in an ImpactedAsset with its bucket assignment."""
        result: List[ImpactedAsset] = []
        for asset in assets:
            asset_type = asset.asset_type.value
            bucket = _ASSET_TYPE_TO_BUCKET.get(asset_type, "external_consumers")
            result.append(
                ImpactedAsset(
                    asset=asset,
                    type=asset_type,
                    system=asset.system.value,
                    bucket=bucket,
                )
            )
        return result

    @staticmethod
    def _fill_buckets(
        assets: List[ImpactedAsset],
    ) -> Dict[str, List[ImpactedAsset]]:
        """Distribute ImpactedAssets into their enterprise bucket lists."""
        buckets: Dict[str, List[ImpactedAsset]] = {b: [] for b in ENTERPRISE_BUCKETS}
        for asset in assets:
            buckets[asset.bucket].append(asset)
        return buckets

    @staticmethod
    def _compute_metrics(
        impacted_assets: List[ImpactedAsset],
        paths: List[List[str]],
    ) -> GraphMetrics:
        """Compute all metrics deterministically from graph data."""
        total = len(impacted_assets)
        critical = sum(
            1 for a in impacted_assets if a.type in _CRITICAL_TYPES
        )
        max_depth = max((len(p) - 1 for p in paths), default=0)
        systems = len({a.system for a in impacted_assets})
        non_empty_buckets = len({a.bucket for a in impacted_assets})

        # Leaf assets: IDs that appear as the last element of any path
        # but never as a non-last element (i.e. no further outgoing edges
        # in any recorded path).
        all_ids = {a.asset.id for a in impacted_assets}
        non_leaf_ids: set = set()
        for path in paths:
            for pid in path[:-1]:
                non_leaf_ids.add(pid)
        leaf_count = len(all_ids - non_leaf_ids)

        return GraphMetrics(
            total_assets=total,
            critical_assets=critical,
            max_depth=max_depth,
            systems_impacted=systems,
            buckets_impacted=non_empty_buckets,
            leaf_assets=leaf_count,
        )
