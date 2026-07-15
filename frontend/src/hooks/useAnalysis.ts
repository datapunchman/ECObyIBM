import { useState, useCallback } from "react";
import { AnalysisService } from "@/services";
import type { AnalysisResult, AnalysisRequest, AsyncStatus, ApiError } from "@/types";

interface UseAnalysisState {
  status: AsyncStatus;
  result: AnalysisResult | null;
  error: ApiError | null;
}

interface UseAnalysisReturn extends UseAnalysisState {
  analyze: (request: string, changeType?: AnalysisRequest["change_type"]) => Promise<void>;
  reset: () => void;
}

export function useAnalysis(): UseAnalysisReturn {
  const [state, setState] = useState<UseAnalysisState>({
    status: "idle",
    result: null,
    error: null,
  });

  const analyze = useCallback(
    async (request: string, changeType?: AnalysisRequest["change_type"]) => {
      setState({ status: "loading", result: null, error: null });
      try {
        const result = await AnalysisService.analyze({
          request,
          change_type: changeType ?? "unknown",
        });
        setState({ status: "success", result, error: null });
      } catch (err) {
        setState({ status: "error", result: null, error: err as ApiError });
      }
    },
    []
  );

  const reset = useCallback(() => {
    setState({ status: "idle", result: null, error: null });
  }, []);

  return { ...state, analyze, reset };
}
