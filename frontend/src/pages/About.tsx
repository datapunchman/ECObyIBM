import React from "react";
import {
  Container,
  Box,
  Typography,
  Paper,
  Grid,
  Chip,
  Divider,
  Table,
  TableBody,
  TableCell,
  TableRow,
} from "@mui/material";

const STACK = [
  { layer: "Frontend",         tech: "React 18 + Vite + TypeScript",              version: "18.3" },
  { layer: "UI Library",       tech: "Material UI (MUI)",                          version: "6.x" },
  { layer: "Graph",            tech: "@xyflow/react (React Flow)",                 version: "12.x" },
  { layer: "HTTP Client",      tech: "Axios",                                      version: "1.x" },
  { layer: "Routing",          tech: "React Router v6",                            version: "6.x" },
  { layer: "AI Engine",        tech: "IBM Granite via watsonx.ai Chat REST API",   version: "—" },
  { layer: "Metadata Engine",  tech: "FastAPI + TMDL / PBIR parsers",              version: "—" },
  { layer: "Graph Engine",     tech: "EnterpriseQueryEngine (BFS, custom)",        version: "—" },
];

const ENDPOINTS = [
  { method: "POST", path: "/analyze",         description: "Run full Granite impact analysis" },
  { method: "GET",  path: "/analyze/preview", description: "Dry-run prompt preview (no IBM credentials needed)" },
  { method: "GET",  path: "/analyze/health",  description: "Liveness and readiness check" },
  { method: "GET",  path: "/analyze/reload",  description: "Invalidate cached analyzer" },
  { method: "GET",  path: "/metadata",        description: "Full metadata payload (tables, columns, measures)" },
];

const About: React.FC = () => (
  <Container maxWidth="lg" sx={{ py: 5, px: { xs: 2, md: 4 } }}>
    <Box mb={5}>
      <Typography variant="overline" color="text.secondary" display="block" mb={1}>
        About
      </Typography>
      <Typography variant="h4" fontWeight={700} gutterBottom>
        Enterprise Change Orchestrator
      </Typography>
      <Typography variant="body1" color="text.secondary" sx={{ maxWidth: 680, lineHeight: 1.75 }}>
        ECO is an enterprise-grade change intelligence platform. It parses the full Power BI
        semantic model and report definitions, builds a dependency graph, and uses IBM Granite
        to generate structured impact analysis — risk level, affected artifacts, deployment
        plan, validation checklist, and rollback instructions.
      </Typography>
    </Box>

    <Grid container spacing={4}>
      <Grid item xs={12} md={7}>
        {/* Tech stack */}
        <Paper variant="outlined" sx={{ p: 3, mb: 3, borderColor: "divider" }}>
          <Typography variant="overline" color="text.secondary" display="block" mb={2}>
            Technology Stack
          </Typography>
          <Table size="small">
            <TableBody>
              {STACK.map(({ layer, tech, version }) => (
                <TableRow key={layer}>
                  <TableCell
                    sx={{
                      fontWeight: 600,
                      color: "text.primary",
                      fontSize: "0.8125rem",
                      width: 160,
                      borderBottom: "1px solid",
                      borderColor: "divider",
                    }}
                  >
                    {layer}
                  </TableCell>
                  <TableCell
                    sx={{
                      color: "text.secondary",
                      fontSize: "0.8125rem",
                      borderBottom: "1px solid",
                      borderColor: "divider",
                    }}
                  >
                    {tech}
                  </TableCell>
                  <TableCell
                    sx={{
                      color: "text.disabled",
                      fontSize: "0.75rem",
                      borderBottom: "1px solid",
                      borderColor: "divider",
                      textAlign: "right",
                    }}
                  >
                    {version}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Paper>

        {/* API endpoints */}
        <Paper variant="outlined" sx={{ p: 3, borderColor: "divider" }}>
          <Typography variant="overline" color="text.secondary" display="block" mb={2}>
            Backend API Endpoints
          </Typography>
          {ENDPOINTS.map(({ method, path, description }) => (
            <Box key={path} display="flex" alignItems="flex-start" gap={1.5} mb={1.5}>
              <Chip
                label={method}
                size="small"
                sx={{
                  bgcolor: method === "POST" ? "#0043ce" : "#005d5d",
                  color: "#fff",
                  fontWeight: 700,
                  fontSize: "0.65rem",
                  letterSpacing: "0.04em",
                  borderRadius: 1,
                  minWidth: 44,
                }}
              />
              <Box>
                <Typography
                  variant="body2"
                  sx={{ fontFamily: '"IBM Plex Mono", monospace', fontWeight: 500, mb: 0.25 }}
                >
                  {path}
                </Typography>
                <Typography variant="caption" color="text.secondary">
                  {description}
                </Typography>
              </Box>
            </Box>
          ))}
        </Paper>
      </Grid>

      <Grid item xs={12} md={5}>
        {/* Architecture diagram (text) */}
        <Paper variant="outlined" sx={{ p: 3, mb: 3, borderColor: "divider" }}>
          <Typography variant="overline" color="text.secondary" display="block" mb={2}>
            Pipeline Architecture
          </Typography>
          {[
            "Power BI PBIP (.pbip)",
            "TMDL + PBIR Parsers",
            "MetadataPayload",
            "MetadataAdapter",
            "EnterpriseGraph",
            "EnterpriseQueryEngine",
            "PromptBuilder (~260 tokens)",
            "IBM Granite (watsonx.ai)",
            "ResponseParser",
            "AnalysisResult → ECO UI",
          ].map((step, i, arr) => (
            <Box key={step}>
              <Box display="flex" alignItems="center" gap={1.5}>
                <Box
                  sx={{
                    width: 24,
                    height: 24,
                    borderRadius: "50%",
                    bgcolor: "#edf5ff",
                    border: "1.5px solid #0f62fe",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    flexShrink: 0,
                    fontSize: "0.65rem",
                    fontWeight: 700,
                    color: "#0043ce",
                  }}
                >
                  {i + 1}
                </Box>
                <Typography variant="body2" color="text.primary" fontWeight={i === arr.length - 1 ? 600 : 400}>
                  {step}
                </Typography>
              </Box>
              {i < arr.length - 1 && (
                <Box
                  sx={{
                    ml: "11px",
                    width: 2,
                    height: 16,
                    bgcolor: "#dde1e6",
                  }}
                />
              )}
            </Box>
          ))}
        </Paper>

        {/* Quick start */}
        <Paper variant="outlined" sx={{ p: 3, borderColor: "divider" }}>
          <Typography variant="overline" color="text.secondary" display="block" mb={2}>
            Quick Start
          </Typography>
          {[
            "# Metadata Engine (port 8000)",
            "uvicorn metadata.engine:app --host 127.0.0.1 --port 8000",
            "",
            "# AI Engine (port 8001)",
            "uvicorn ai.engine:app --host 127.0.0.1 --port 8001",
            "",
            "# Frontend (port 3000)",
            "cd frontend && npm install && npm run dev",
          ].map((line, i) => (
            <Typography
              key={i}
              variant="body2"
              sx={{
                fontFamily: '"IBM Plex Mono", monospace',
                fontSize: "0.78rem",
                color: line.startsWith("#") ? "#697077" : "#161616",
                lineHeight: 1.8,
                whiteSpace: "pre",
              }}
            >
              {line || "\u00A0"}
            </Typography>
          ))}
        </Paper>
      </Grid>
    </Grid>

    <Divider sx={{ my: 5 }} />
    <Typography variant="caption" color="text.disabled" display="block" textAlign="center">
      ECO — Enterprise Change Orchestrator · Powered by IBM Granite · BOB Hackathon
    </Typography>
  </Container>
);

export default About;
