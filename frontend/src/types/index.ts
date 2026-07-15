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

// ── Impact analysis response (outbound) ─────────────────────────────────────

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
