import React, { useEffect } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import {
  Container,
  Box,
  Typography,
  Grid,
  Paper,
  Button,
  Chip,
  Divider,
  List,
  ListItem,
  ListItemText,
  Alert,
} from "@mui/material";
import ArrowBackIcon from "@mui/icons-material/ArrowBack";
import AccountTreeOutlinedIcon from "@mui/icons-material/AccountTreeOutlined";
import {
  RiskCard,
  SystemImpactCard,
  SummaryCard,
  DeploymentTimeline,
} from "@/components";
import type { AnalysisResult } from "@/types";

interface LocationState {
  result: AnalysisResult;
  request: string;
}

const SYSTEM_ORDER = ["database", "sql", "databricks", "pipeline", "powerbi", "api"];

const Analysis: React.FC = () => {
  const location = useLocation();
  const navigate = useNavigate();
  const state = location.state as LocationState | null;

  useEffect(() => {
    if (!state?.result) navigate("/");
  }, [state, navigate]);

  if (!state?.result) return null;

  const { result, request } = state;
  const { impact_analysis: ia } = result;

  // Build system → affected names map from impacted artifact lists
  const systemItems: Record<string, string[]> = {
    database: [],
    sql: [],
    databricks: [],
    pipeline: [],
    powerbi: [
      ...ia.affected_tables,
      ...ia.affected_measures,
      ...ia.affected_reports,
      ...ia.affected_columns,
    ],
    api: [],
  };

  return (
    <Container maxWidth="xl" sx={{ py: 4, px: { xs: 2, md: 4 } }}>
      {/* Breadcrumb */}
      <Box display="flex" alignItems="center" gap={1.5} mb={3}>
        <Button
          startIcon={<ArrowBackIcon />}
          size="small"
          variant="text"
          sx={{ color: "text.secondary" }}
          onClick={() => navigate("/")}
        >
          New Analysis
        </Button>
        <Typography color="text.disabled" variant="body2">
          /
        </Typography>
        <Typography variant="body2" color="text.primary" fontWeight={500}>
          Impact Analysis
        </Typography>
      </Box>

      {/* Request summary */}
      <Paper variant="outlined" sx={{ p: 3, mb: 3, borderColor: "divider" }}>
        <Typography variant="overline" color="text.secondary">
          Change Request
        </Typography>
        <Typography
          variant="body1"
          sx={{
            mt: 0.5,
            fontFamily: '"IBM Plex Mono", monospace',
            bgcolor: "#f2f4f8",
            px: 2,
            py: 1.25,
            borderRadius: 1,
            fontSize: "0.9rem",
          }}
        >
          {request}
        </Typography>
        <Box display="flex" gap={1} mt={1.5}>
          <Chip
            label={result.model_id}
            size="small"
            variant="outlined"
            sx={{ fontFamily: '"IBM Plex Mono", monospace', fontSize: "0.7rem", borderRadius: 1 }}
          />
          <Chip
            label={`~${result.token_estimate} tokens`}
            size="small"
            variant="outlined"
            sx={{ fontSize: "0.7rem", borderRadius: 1 }}
          />
          {!result.parse_success && (
            <Chip
              label="Parse warning"
              size="small"
              color="warning"
              sx={{ fontSize: "0.7rem", borderRadius: 1 }}
            />
          )}
        </Box>
      </Paper>

      {!result.parse_success && (
        <Alert severity="warning" sx={{ mb: 3 }}>
          The AI response was partially parsed. Some fields may be incomplete.
        </Alert>
      )}

      <Grid container spacing={3}>
        {/* Left column */}
        <Grid item xs={12} lg={8}>
          {/* Risk */}
          <RiskCard
            riskLevel={ia.risk_level}
            rationale={ia.risk_rationale}
            dependenciesImpacted={ia.dependencies_impacted}
          />

          <Box mt={3}>
            <SummaryCard
              title="Executive Summary"
              content={ia.executive_summary}
            />
          </Box>

          <Box mt={3}>
            <SummaryCard
              title="Impact Analysis"
              content={ia.impact_analysis}
            />
          </Box>

          {/* System Impact Cards */}
          <Box mt={3}>
            <Typography
              variant="overline"
              color="text.secondary"
              display="block"
              mb={2}
            >
              Impact by System
            </Typography>
            <Grid container spacing={2}>
              {SYSTEM_ORDER.map((sys) => {
                const items = systemItems[sys] ?? [];
                if (!items.length) return null;
                return (
                  <Grid item xs={12} sm={6} key={sys}>
                    <SystemImpactCard system={sys} items={items} />
                  </Grid>
                );
              })}
            </Grid>
          </Box>
        </Grid>

        {/* Right column */}
        <Grid item xs={12} lg={4}>
          {/* Affected artifacts */}
          <Paper variant="outlined" sx={{ p: 2.5, mb: 3, borderColor: "divider" }}>
            <Typography variant="overline" color="text.secondary" display="block" mb={1.5}>
              Affected Artifacts
            </Typography>

            {(
              [
                { label: "Tables", items: ia.affected_tables },
                { label: "Columns", items: ia.affected_columns },
                { label: "Measures", items: ia.affected_measures },
                { label: "Reports", items: ia.affected_reports },
              ] as const
            ).map(({ label, items }) =>
              items.length ? (
                <Box key={label} mb={1.5}>
                  <Typography
                    variant="caption"
                    fontWeight={600}
                    color="text.secondary"
                    display="block"
                    mb={0.5}
                  >
                    {label}
                  </Typography>
                  <Box display="flex" flexWrap="wrap" gap={0.5}>
                    {items.map((item) => (
                      <Chip
                        key={item}
                        label={item}
                        size="small"
                        variant="outlined"
                        sx={{
                          fontFamily: '"IBM Plex Mono", monospace',
                          fontSize: "0.7rem",
                          borderRadius: 1,
                          borderColor: "#0f62fe",
                          color: "#0043ce",
                        }}
                      />
                    ))}
                  </Box>
                </Box>
              ) : null
            )}
          </Paper>

          {/* Deployment plan */}
          <DeploymentTimeline steps={ia.deployment_plan} title="Deployment Plan" />

          {/* Validation checklist */}
          {ia.validation_checklist.length > 0 && (
            <Paper variant="outlined" sx={{ p: 2.5, mt: 3, borderColor: "divider" }}>
              <Typography
                variant="overline"
                color="text.secondary"
                display="block"
                mb={1.5}
              >
                Validation Checklist
              </Typography>
              <List dense disablePadding>
                {ia.validation_checklist.map((check, i) => (
                  <ListItem key={i} disableGutters sx={{ alignItems: "flex-start", py: 0.5 }}>
                    <ListItemText
                      primary={check}
                      primaryTypographyProps={{
                        variant: "body2",
                        color: "text.secondary",
                      }}
                    />
                  </ListItem>
                ))}
              </List>
            </Paper>
          )}

          {/* Rollback plan */}
          {ia.rollback_plan.length > 0 && (
            <Box mt={3}>
              <DeploymentTimeline
                steps={ia.rollback_plan}
                title="Rollback Plan"
              />
            </Box>
          )}

          <Divider sx={{ my: 3 }} />

          {/* View graph */}
          <Button
            variant="outlined"
            fullWidth
            startIcon={<AccountTreeOutlinedIcon />}
            onClick={() =>
              navigate("/graph", {
                state: { result, request },
              })
            }
          >
            View Dependency Graph
          </Button>
        </Grid>
      </Grid>
    </Container>
  );
};

export default Analysis;
