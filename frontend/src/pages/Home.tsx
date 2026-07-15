import React, { useState, useRef } from "react";
import { useNavigate } from "react-router-dom";
import {
  Box,
  Container,
  Typography,
  TextField,
  Button,
  Paper,
  Chip,
  Grid,
  Divider,
} from "@mui/material";
import ArrowForwardIcon from "@mui/icons-material/ArrowForward";
import ShieldOutlinedIcon from "@mui/icons-material/ShieldOutlined";
import AccountTreeOutlinedIcon from "@mui/icons-material/AccountTreeOutlined";
import BoltOutlinedIcon from "@mui/icons-material/BoltOutlined";
import { LoadingOverlay } from "@/components";
import { useAnalysis } from "@/hooks/useAnalysis";

const EXAMPLE_REQUESTS = [
  "Rename Customer_ID to Client_ID in the Customers table",
  "Remove the DOB column from Customers",
  "Drop the SalesTerritory table",
  "Add a new NetRevenue column to sales_dashboard",
  "Rename the Revenue column in sales_dashboard to GrossRevenue",
];

const FEATURE_TILES = [
  {
    Icon: ShieldOutlinedIcon,
    title: "Risk Assessment",
    body: "Automatic risk scoring based on downstream dependency count and production report impact.",
  },
  {
    Icon: AccountTreeOutlinedIcon,
    title: "Dependency Graph",
    body: "Visual map of every artifact affected across Database, Databricks, and Power BI layers.",
  },
  {
    Icon: BoltOutlinedIcon,
    title: "Granite AI",
    body: "IBM Granite generates deployment plans, validation checklists, and rollback instructions.",
  },
];

const Home: React.FC = () => {
  const [request, setRequest] = useState("");
  const navigate = useNavigate();
  const { analyze, status } = useAnalysis();
  const resultRef = useRef<AnalysisResult | null>(null);

  const handleAnalyze = async () => {
    if (!request.trim()) return;
    const result = await (async () => {
      const { AnalysisService } = await import("@/services");
      return AnalysisService.analyze({ request, change_type: "unknown" });
    })();
    resultRef.current = result;
    void analyze; // suppress unused warning — navigation carries the result via state
    navigate("/analysis", { state: { result, request } });
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && e.ctrlKey) void handleAnalyze();
  };

  return (
    <>
      <LoadingOverlay open={status === "loading"} />

      {/* Hero */}
      <Box
        sx={{
          bgcolor: "#161616",
          borderBottom: "1px solid #393939",
          px: 0,
          pt: { xs: 8, md: 10 },
          pb: { xs: 7, md: 9 },
        }}
      >
        <Container maxWidth="md">
          <Chip
            label="Enterprise Change Intelligence"
            size="small"
            sx={{
              bgcolor: "#0043ce",
              color: "#fff",
              fontWeight: 600,
              fontSize: "0.7rem",
              letterSpacing: "0.05em",
              mb: 3,
              borderRadius: 1,
            }}
          />

          <Typography
            variant="h2"
            sx={{
              color: "#f2f4f8",
              fontWeight: 700,
              fontSize: { xs: "2rem", md: "2.75rem" },
              lineHeight: 1.15,
              mb: 2,
              letterSpacing: "-0.02em",
            }}
          >
            Understand every downstream
            <br />
            impact before deployment.
          </Typography>

          <Typography
            variant="body1"
            sx={{
              color: "#8d8d8d",
              maxWidth: 600,
              mb: 5,
              lineHeight: 1.7,
            }}
          >
            ECO analyses your proposed data platform change, traverses the full
            dependency graph, and returns a structured risk assessment — powered
            by IBM Granite.
          </Typography>

          {/* Input card */}
          <Paper
            sx={{
              p: 3,
              bgcolor: "#262626",
              border: "1px solid #525252",
              borderRadius: 1,
            }}
          >
            <Typography
              variant="overline"
              sx={{ color: "#8d8d8d", display: "block", mb: 1 }}
            >
              Describe your proposed change
            </Typography>

            <TextField
              multiline
              minRows={3}
              maxRows={8}
              fullWidth
              placeholder={`e.g. "${EXAMPLE_REQUESTS[0]}"`}
              value={request}
              onChange={(e) => setRequest(e.target.value)}
              onKeyDown={handleKeyDown}
              variant="outlined"
              sx={{
                mb: 2,
                "& .MuiOutlinedInput-root": {
                  bgcolor: "#161616",
                  color: "#f2f4f8",
                  fontFamily: '"IBM Plex Mono", monospace',
                  fontSize: "0.9rem",
                  "& fieldset": { borderColor: "#393939" },
                  "&:hover fieldset": { borderColor: "#525252" },
                  "&.Mui-focused fieldset": { borderColor: "#0f62fe" },
                },
              }}
              inputProps={{ "aria-label": "Change request" }}
            />

            <Box display="flex" alignItems="center" justifyContent="space-between" gap={2}>
              <Typography variant="caption" sx={{ color: "#525252" }}>
                Ctrl + Enter to submit
              </Typography>
              <Button
                variant="contained"
                size="large"
                endIcon={<ArrowForwardIcon />}
                disabled={!request.trim() || status === "loading"}
                onClick={() => void handleAnalyze()}
                sx={{ px: 4, py: 1.25, fontWeight: 600, minWidth: 180 }}
              >
                Analyze Impact
              </Button>
            </Box>
          </Paper>

          {/* Quick examples */}
          <Box mt={3}>
            <Typography variant="caption" sx={{ color: "#525252", mr: 1.5 }}>
              Try:
            </Typography>
            {EXAMPLE_REQUESTS.slice(0, 3).map((ex) => (
              <Chip
                key={ex}
                label={ex}
                size="small"
                variant="outlined"
                onClick={() => setRequest(ex)}
                sx={{
                  mr: 1,
                  mb: 0.75,
                  color: "#8d8d8d",
                  borderColor: "#393939",
                  cursor: "pointer",
                  borderRadius: 1,
                  fontSize: "0.72rem",
                  "&:hover": { borderColor: "#0f62fe", color: "#78a9ff" },
                }}
              />
            ))}
          </Box>
        </Container>
      </Box>

      {/* Feature tiles */}
      <Container maxWidth="md" sx={{ py: 8 }}>
        <Typography
          variant="overline"
          sx={{ color: "text.secondary", display: "block", mb: 3 }}
        >
          What ECO gives you
        </Typography>
        <Grid container spacing={3}>
          {FEATURE_TILES.map(({ Icon, title, body }) => (
            <Grid item xs={12} md={4} key={title}>
              <Paper
                variant="outlined"
                sx={{ p: 3, height: "100%", borderColor: "divider" }}
              >
                <Box
                  sx={{
                    width: 36,
                    height: 36,
                    bgcolor: "#edf5ff",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    borderRadius: 1,
                    mb: 2,
                  }}
                >
                  <Icon sx={{ fontSize: 20, color: "#0043ce" }} />
                </Box>
                <Typography
                  variant="subtitle2"
                  sx={{ fontWeight: 600, mb: 0.75 }}
                >
                  {title}
                </Typography>
                <Typography variant="body2" color="text.secondary">
                  {body}
                </Typography>
              </Paper>
            </Grid>
          ))}
        </Grid>

        <Divider sx={{ my: 6 }} />

        <Box textAlign="center">
          <Typography
            variant="body2"
            color="text.secondary"
            sx={{ maxWidth: 520, mx: "auto" }}
          >
            ECO connects your Power BI Semantic Model, Databricks Gold layer,
            and SQL source into a single impact graph — so you can ship with
            confidence.
          </Typography>
        </Box>
      </Container>
    </>
  );
};

// silence TS unused import
import type { AnalysisResult } from "@/types";
export default Home;
