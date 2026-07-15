import React from "react";
import { useLocation, useNavigate } from "react-router-dom";
import {
  Container,
  Box,
  Typography,
  Button,
  Paper,
  Chip,
  Alert,
} from "@mui/material";
import ArrowBackIcon from "@mui/icons-material/ArrowBack";
import { DependencyTree } from "@/components";
import type { AnalysisResult, GraphAsset } from "@/types";

interface LocationState {
  result: AnalysisResult;
  request: string;
}

const DependencyGraph: React.FC = () => {
  const location = useLocation();
  const navigate = useNavigate();
  const state = location.state as LocationState | null;

  if (!state?.result) {
    return (
      <Container maxWidth="md" sx={{ py: 8, textAlign: "center" }}>
        <Alert severity="info" sx={{ mb: 3 }}>
          No analysis data available. Run an analysis first.
        </Alert>
        <Button
          variant="contained"
          onClick={() => navigate("/")}
        >
          Go to Home
        </Button>
      </Container>
    );
  }

  const { result, request } = state;
  const { impact_analysis: ia } = result;

  // Reconstruct graph assets from the analysis result
  const assets: GraphAsset[] = [
    ...ia.affected_tables.map((name) => ({
      id: `table::${name}`,
      name,
      asset_type: "table",
      system: "powerbi" as const,
      properties: {},
    })),
    ...ia.affected_measures.map((name) => ({
      id: `measure::${name}`,
      name,
      asset_type: "measure",
      system: "powerbi" as const,
      properties: {},
    })),
    ...ia.affected_reports.map((name) => ({
      id: `report::${name}`,
      name,
      asset_type: "report",
      system: "powerbi" as const,
      properties: {},
    })),
  ];

  // Simple path heuristic: tables → measures → reports
  const paths: string[][] = [];
  for (const tbl of ia.affected_tables) {
    for (const meas of ia.affected_measures) {
      for (const rpt of ia.affected_reports) {
        paths.push([`table::${tbl}`, `measure::${meas}`, `report::${rpt}`]);
      }
    }
  }

  return (
    <Container maxWidth="xl" sx={{ py: 4, px: { xs: 2, md: 4 } }}>
      {/* Breadcrumb */}
      <Box display="flex" alignItems="center" gap={1.5} mb={3}>
        <Button
          startIcon={<ArrowBackIcon />}
          size="small"
          variant="text"
          sx={{ color: "text.secondary" }}
          onClick={() => navigate("/analysis", { state })}
        >
          Back to Analysis
        </Button>
      </Box>

      <Box display="flex" alignItems="flex-start" justifyContent="space-between" mb={3}>
        <Box>
          <Typography variant="h5" fontWeight={600} gutterBottom>
            Dependency Graph
          </Typography>
          <Typography variant="body2" color="text.secondary">
            Downstream impact for:{" "}
            <Box
              component="span"
              sx={{ fontFamily: '"IBM Plex Mono", monospace', color: "primary.main" }}
            >
              {request}
            </Box>
          </Typography>
        </Box>
        <Box display="flex" gap={1}>
          <Chip
            label={`${assets.length} assets`}
            size="small"
            variant="outlined"
            sx={{ borderRadius: 1 }}
          />
          <Chip
            label={ia.risk_level.toUpperCase()}
            size="small"
            color={
              ia.risk_level === "critical" || ia.risk_level === "high"
                ? "error"
                : ia.risk_level === "medium"
                ? "warning"
                : "success"
            }
            sx={{ borderRadius: 1 }}
          />
        </Box>
      </Box>

      <Paper variant="outlined" sx={{ p: 0, overflow: "hidden", borderColor: "divider" }}>
        <DependencyTree assets={assets} paths={paths} height="calc(100vh - 280px)" />
      </Paper>

      <Box mt={2} display="flex" gap={2} flexWrap="wrap">
        {(["powerbi", "databricks", "sql", "database", "pipeline", "api"] as const).map(
          (sys) => (
            <Box key={sys} display="flex" alignItems="center" gap={0.75}>
              <Box
                sx={{
                  width: 10,
                  height: 10,
                  borderRadius: "50%",
                  bgcolor: {
                    powerbi:    "#8a3ffc",
                    databricks: "#ff6200",
                    sql:        "#005d5d",
                    database:   "#6929c4",
                    pipeline:   "#0043ce",
                    api:        "#007d79",
                  }[sys],
                }}
              />
              <Typography variant="caption" color="text.secondary" textTransform="capitalize">
                {sys}
              </Typography>
            </Box>
          )
        )}
      </Box>
    </Container>
  );
};

export default DependencyGraph;
