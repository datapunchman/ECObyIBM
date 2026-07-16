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
} from "@mui/material";
import ArrowBackIcon from "@mui/icons-material/ArrowBack";
import AccountTreeOutlinedIcon from "@mui/icons-material/AccountTreeOutlined";
import StorageIcon from "@mui/icons-material/Storage";
import TableChartOutlinedIcon from "@mui/icons-material/TableChartOutlined";
import CodeOutlinedIcon from "@mui/icons-material/CodeOutlined";
import AccountTreeIcon from "@mui/icons-material/AccountTree";
import BarChartOutlinedIcon from "@mui/icons-material/BarChartOutlined";
import FolderOutlinedIcon from "@mui/icons-material/FolderOutlined";
import FunctionsOutlinedIcon from "@mui/icons-material/FunctionsOutlined";
import SettingsOutlinedIcon from "@mui/icons-material/SettingsOutlined";
import ViewListOutlinedIcon from "@mui/icons-material/ViewListOutlined";
import {
  RiskCard,
  SummaryCard,
  DeploymentTimeline,
} from "@/components";
import type { V2AnalysisResult, V2ImpactedAsset } from "@/types";

interface LocationState {
  result: V2AnalysisResult;
  request: string;
}

// ---------------------------------------------------------------------------
// Bucket display configuration (label, icon, colour)
// ---------------------------------------------------------------------------

interface BucketConfig {
  label: string;
  color: string;
  bgColor: string;
  Icon: React.ElementType;
}

const BUCKET_CONFIG: Record<string, BucketConfig> = {
  database_tables:      { label: "Database Tables",      color: "#6929c4", bgColor: "#f6f2ff", Icon: StorageIcon },
  views:                { label: "SQL Views",             color: "#005d5d", bgColor: "#d9fbfb", Icon: ViewListOutlinedIcon },
  materialized_views:   { label: "Materialized Views",   color: "#005d5d", bgColor: "#d9fbfb", Icon: ViewListOutlinedIcon },
  stored_procedures:    { label: "Stored Procedures",    color: "#8a3800", bgColor: "#fff2e8", Icon: SettingsOutlinedIcon },
  functions:            { label: "SQL Functions",         color: "#8a3800", bgColor: "#fff2e8", Icon: FunctionsOutlinedIcon },
  databricks_notebooks: { label: "Databricks Notebooks", color: "#ff6200", bgColor: "#fff2e8", Icon: CodeOutlinedIcon },
  spark_jobs:           { label: "Spark Jobs",            color: "#ff6200", bgColor: "#fff2e8", Icon: CodeOutlinedIcon },
  delta_live_tables:    { label: "Delta Live Tables",     color: "#c87137", bgColor: "#fff8f0", Icon: TableChartOutlinedIcon },
  unity_catalog:        { label: "Unity Catalog",         color: "#c87137", bgColor: "#fff8f0", Icon: StorageIcon },
  pipelines:            { label: "Pipelines",             color: "#0043ce", bgColor: "#edf5ff", Icon: AccountTreeIcon },
  data_factory:         { label: "Data Factory",          color: "#0043ce", bgColor: "#edf5ff", Icon: AccountTreeIcon },
  airflow:              { label: "Airflow DAGs",           color: "#0043ce", bgColor: "#edf5ff", Icon: AccountTreeIcon },
  fabric_pipelines:     { label: "Fabric Pipelines",      color: "#0043ce", bgColor: "#edf5ff", Icon: AccountTreeIcon },
  semantic_models:      { label: "Semantic Models",       color: "#6929c4", bgColor: "#f6f2ff", Icon: BarChartOutlinedIcon },
  powerbi_reports:      { label: "Power BI Reports",      color: "#be95ff", bgColor: "#f6f2ff", Icon: BarChartOutlinedIcon },
  dashboards:           { label: "Dashboards",            color: "#be95ff", bgColor: "#f6f2ff", Icon: BarChartOutlinedIcon },
  apis:                 { label: "APIs",                  color: "#007d79", bgColor: "#d9fbfb", Icon: StorageIcon },
  external_consumers:   { label: "ADLS / External",       color: "#697077", bgColor: "#f2f4f8", Icon: FolderOutlinedIcon },
};

// Ordered list of buckets to display (same order as the backend ENTERPRISE_BUCKETS)
const DISPLAY_BUCKETS: (keyof typeof BUCKET_CONFIG)[] = [
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
];

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

interface BucketCardProps {
  bucketKey: string;
  assets: V2ImpactedAsset[];
}

const BucketCard: React.FC<BucketCardProps> = ({ bucketKey, assets }) => {
  if (!assets.length) return null;
  const cfg = BUCKET_CONFIG[bucketKey] ?? {
    label: bucketKey.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase()),
    color: "#697077",
    bgColor: "#f2f4f8",
    Icon: StorageIcon,
  };
  const { Icon } = cfg;

  return (
    <Paper variant="outlined" sx={{ p: 2.5, borderColor: "divider", height: "100%" }}>
      <Box display="flex" alignItems="center" gap={1} mb={1.5}>
        <Box
          sx={{
            width: 32,
            height: 32,
            borderRadius: 1,
            bgcolor: cfg.bgColor,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            flexShrink: 0,
          }}
        >
          <Icon sx={{ fontSize: 18, color: cfg.color }} />
        </Box>
        <Box>
          <Typography variant="overline" sx={{ color: cfg.color, lineHeight: 1 }}>
            {cfg.label}
          </Typography>
          <Typography variant="caption" display="block" color="text.secondary">
            {assets.length} affected
          </Typography>
        </Box>
      </Box>
      <Divider sx={{ mb: 1.5 }} />
      <Box display="flex" flexWrap="wrap" gap={0.75}>
        {assets.map((a) => (
          <Chip
            key={a.id}
            label={a.asset}
            size="small"
            variant="outlined"
            sx={{
              fontFamily: '"IBM Plex Mono", monospace',
              fontSize: "0.72rem",
              borderColor: cfg.color,
              color: cfg.color,
              borderRadius: 1,
            }}
          />
        ))}
      </Box>
    </Paper>
  );
};

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

const Analysis: React.FC = () => {
  const location = useLocation();
  const navigate = useNavigate();
  const state = location.state as LocationState | null;

  useEffect(() => {
    if (!state?.result) navigate("/");
  }, [state, navigate]);

  if (!state?.result) return null;

  const { result, request } = state;
  const ga = result.graph_analysis;
  const llm = result.llm_summary;
  const src = result.source_asset;
  const metrics = ga.metrics;

  // All non-empty buckets for the "Impact by System" section
  const populatedBuckets = DISPLAY_BUCKETS.filter(
    (k) => (ga[k as keyof typeof ga] as V2ImpactedAsset[])?.length > 0
  );

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
        <Typography color="text.disabled" variant="body2">/</Typography>
        <Typography variant="body2" color="text.primary" fontWeight={500}>
          Impact Analysis
        </Typography>
      </Box>

      {/* Change request banner */}
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

        {/* Source asset + metrics chips */}
        <Box display="flex" flexWrap="wrap" gap={1} mt={1.5}>
          {src.name && (
            <Chip
              label={`Source: ${src.name} (${src.type ?? "unknown"})`}
              size="small"
              variant="outlined"
              sx={{ fontFamily: '"IBM Plex Mono", monospace', fontSize: "0.7rem", borderRadius: 1, borderColor: "#0f62fe", color: "#0043ce" }}
            />
          )}
          <Chip
            label={`${metrics.total_assets} assets impacted`}
            size="small"
            variant="outlined"
            sx={{ fontSize: "0.7rem", borderRadius: 1 }}
          />
          <Chip
            label={`${metrics.systems_impacted} systems`}
            size="small"
            variant="outlined"
            sx={{ fontSize: "0.7rem", borderRadius: 1 }}
          />
          <Chip
            label={`${metrics.critical_assets} critical`}
            size="small"
            color={metrics.critical_assets > 0 ? "warning" : "default"}
            sx={{ fontSize: "0.7rem", borderRadius: 1 }}
          />
          <Chip
            label={`depth ${metrics.max_depth}`}
            size="small"
            variant="outlined"
            sx={{ fontSize: "0.7rem", borderRadius: 1 }}
          />
          <Chip
            label="graph-grounded · confidence 1.0"
            size="small"
            color="success"
            variant="outlined"
            sx={{ fontSize: "0.7rem", borderRadius: 1 }}
          />
        </Box>
      </Paper>

      <Grid container spacing={3}>
        {/* ── Left column ── */}
        <Grid item xs={12} lg={8}>
          <RiskCard
            riskLevel={llm.risk_level}
            rationale={llm.risk_rationale}
            dependenciesImpacted={metrics.total_assets}
          />

          <Box mt={3}>
            <SummaryCard title="Executive Summary" content={llm.executive_summary} />
          </Box>

          <Box mt={3}>
            <SummaryCard title="Risk Rationale" content={llm.risk_rationale} />
          </Box>

          {/* Impact by System — all non-empty enterprise buckets */}
          {populatedBuckets.length > 0 && (
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
                {populatedBuckets.map((key) => (
                  <Grid item xs={12} sm={6} key={key}>
                    <BucketCard
                      bucketKey={key}
                      assets={ga[key as keyof typeof ga] as V2ImpactedAsset[]}
                    />
                  </Grid>
                ))}
              </Grid>
            </Box>
          )}

          {populatedBuckets.length === 0 && (
            <Paper variant="outlined" sx={{ p: 3, mt: 3, borderColor: "divider" }}>
              <Typography variant="body2" color="text.secondary">
                No downstream assets were found in the enterprise graph for this change.
              </Typography>
            </Paper>
          )}
        </Grid>

        {/* ── Right column ── */}
        <Grid item xs={12} lg={4}>
          {/* Deployment plan */}
          <DeploymentTimeline steps={llm.deployment_plan} title="Deployment Plan" />

          {/* Validation checklist */}
          {llm.validation_checklist.length > 0 && (
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
                {llm.validation_checklist.map((check, i) => (
                  <ListItem key={i} disableGutters sx={{ alignItems: "flex-start", py: 0.5 }}>
                    <ListItemText
                      primary={check}
                      primaryTypographyProps={{ variant: "body2", color: "text.secondary" }}
                    />
                  </ListItem>
                ))}
              </List>
            </Paper>
          )}

          {/* Rollback plan */}
          {llm.rollback_plan.length > 0 && (
            <Box mt={3}>
              <DeploymentTimeline steps={llm.rollback_plan} title="Rollback Plan" />
            </Box>
          )}

          <Divider sx={{ my: 3 }} />

          {/* View graph */}
          <Button
            variant="outlined"
            fullWidth
            startIcon={<AccountTreeOutlinedIcon />}
            onClick={() => navigate("/graph", { state: { result, request } })}
          >
            View Dependency Graph
          </Button>
        </Grid>
      </Grid>
    </Container>
  );
};

export default Analysis;
