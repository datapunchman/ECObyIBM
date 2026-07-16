// -----------------------------------------------------------------------------
// ECO — Enterprise Change Orchestrator
// API response types (mirrors the Python backend models exactly)
// -----------------------------------------------------------------------------

// ── Change request (inbound) ─────────────────────────────────────────────────

export type ChangeType =
  | "schema"
  | "measure"
  | "relationship"
  | "report"
  | "data"
  | "unknown";

export interface AnalysisRequest {
  request: string;
  change_type?: ChangeType;
  context?: Record<string, string>;
}

// ── v1 Impact analysis response (outbound) ───────────────────────────────────

export type RiskLevel = "low" | "medium" | "high" | "critical";

export interface ImpactAnalysis {
  executive_summary: string;
  risk_level: RiskLevel;
  risk_rationale: string;
  affected_tables: string[];
  affected_columns: string[];
  affected_measures: string[];
  affected_reports: string[];
  impact_analysis: string;
  deployment_plan: string[];
  validation_checklist: string[];
  rollback_plan: string[];
  dependencies_impacted: number;
}

export interface AnalysisResult {
  request: AnalysisRequest;
  token_estimate: number;
  model_id: string;
  parse_success: boolean;
  impact_analysis: ImpactAnalysis;
}

// ── v2 Graph-grounded response ────────────────────────────────────────────────

/** A single impacted asset as returned by the v2 enterprise graph traversal. */
export interface V2ImpactedAsset {
  id: string;
  asset: string;
  type: string;
  system: string;
  bucket: string;
  discovered_by: string;
  confidence: number;
}

/** Deterministic metrics computed by the graph traversal (no LLM). */
export interface V2GraphMetrics {
  total_assets: number;
  critical_assets: number;
  max_depth: number;
  systems_impacted: number;
  buckets_impacted: number;
  leaf_assets: number;
}

/** The 19 enterprise buckets + metrics + dependency paths. */
export interface V2GraphAnalysis {
  database_tables:      V2ImpactedAsset[];
  views:                V2ImpactedAsset[];
  materialized_views:   V2ImpactedAsset[];
  stored_procedures:    V2ImpactedAsset[];
  functions:            V2ImpactedAsset[];
  databricks_notebooks: V2ImpactedAsset[];
  spark_jobs:           V2ImpactedAsset[];
  delta_live_tables:    V2ImpactedAsset[];
  unity_catalog:        V2ImpactedAsset[];
  pipelines:            V2ImpactedAsset[];
  data_factory:         V2ImpactedAsset[];
  airflow:              V2ImpactedAsset[];
  fabric_pipelines:     V2ImpactedAsset[];
  semantic_models:      V2ImpactedAsset[];
  powerbi_reports:      V2ImpactedAsset[];
  dashboards:           V2ImpactedAsset[];
  apis:                 V2ImpactedAsset[];
  external_consumers:   V2ImpactedAsset[];
  metrics:              V2GraphMetrics;
  dependency_paths:     string[][];
}

/** Granite's reasoning output (no dependency discovery).
 *  parse_v2() returns exactly these 6 keys — impact_analysis is NOT present. */
export interface V2LlmSummary {
  risk_level: RiskLevel;
  risk_rationale: string;
  executive_summary: string;
  deployment_plan: string[];
  validation_checklist: string[];
  rollback_plan: string[];
}

export interface V2ChangeRequest {
  original_request: string;
  change_type: string;
  target_name: string | null;
  new_name: string | null;
  table_name: string | null;
}

export interface V2SourceAsset {
  id: string | null;
  name: string | null;
  type: string | null;
  system: string | null;
}

/** Full v2 analysis result returned by POST /analyze/v2. */
export interface V2AnalysisResult {
  change_request: V2ChangeRequest;
  source_asset: V2SourceAsset;
  graph_analysis: V2GraphAnalysis;
  llm_summary: V2LlmSummary;
}

// ── Health check ─────────────────────────────────────────────────────────────

export interface HealthStatus {
  status: "ok" | "degraded";
  metadata_api: "healthy" | "unreachable";
  ibm_credentials: "configured" | "missing";
  ibm_vars: Record<string, boolean>;
  model_id: string;
  service: string;
}

// ── Prompt preview ───────────────────────────────────────────────────────────

export interface PromptSection {
  heading: string;
  content: string;
}

export interface PromptPackage {
  request: AnalysisRequest;
  token_estimate: number;
  sections: PromptSection[];
  prompt_text: string;
}

// ── Graph / dependency types (for React Flow) ────────────────────────────────

export type SystemType =
  | "database"
  | "sql"
  | "databricks"
  | "pipeline"
  | "powerbi"
  | "api";

export interface GraphAsset {
  id: string;
  name: string;
  asset_type: string;
  system: SystemType;
  properties: Record<string, unknown>;
}

export interface GraphRelationship {
  source: string;
  target: string;
  relationship: string;
}

// ── UI state ─────────────────────────────────────────────────────────────────

export type AsyncStatus = "idle" | "loading" | "success" | "error";

export interface ApiError {
  message: string;
  status?: number;
  detail?: string;
}
