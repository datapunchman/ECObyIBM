import client from "./apiClient";
import type {
  AnalysisRequest,
  AnalysisResult,
  HealthStatus,
  PromptPackage,
} from "@/types";

const AnalysisService = {
  /**
   * POST /analyze — run the full Granite impact analysis pipeline.
   */
  analyze: (request: AnalysisRequest): Promise<AnalysisResult> =>
    client
      .post<AnalysisResult>("/analyze", request)
      .then((r) => r.data),

  /**
   * GET /analyze/preview — dry-run prompt preview, no IBM credentials needed.
   */
  preview: (): Promise<PromptPackage> =>
    client
      .get<PromptPackage>("/analyze/preview")
      .then((r) => r.data),

  /**
   * GET /analyze/health — liveness + readiness check.
   */
  health: (): Promise<HealthStatus> =>
    client
      .get<HealthStatus>("/analyze/health")
      .then((r) => r.data),

  /**
   * GET /analyze/reload — invalidate the cached analyzer.
   */
  reload: (): Promise<{ status: string; message: string }> =>
    client
      .get<{ status: string; message: string }>("/analyze/reload")
      .then((r) => r.data),
};

export default AnalysisService;
