/**
 * DependencyGraph — the signature full-screen graph page.
 *
 * Entire viewport is the interactive EnterpriseFlow graph. Renders ONLY
 * real /analyze/v2 data passed via router state — with no analysis
 * available it shows a plain empty state (no fabricated demo data).
 */
import React, { useMemo } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { Box, Typography, Button } from "@mui/material";
import { motion } from "framer-motion";
import { ArrowLeft } from "lucide-react";
import { T, SYSTEM_HUES } from "@/assets/theme";
import EnterpriseFlow, { buildEcoGraph } from "@/components/EnterpriseFlow";
import ParticleBackground from "@/components/ParticleBackground";
import type { V2AnalysisResult } from "@/types";

interface LocationState {
  result: V2AnalysisResult;
  request: string;
}

const DependencyGraph: React.FC = () => {
  const location = useLocation();
  const navigate = useNavigate();
  const state = location.state as LocationState | null;

  const graph = useMemo(
    () => (state?.result ? buildEcoGraph(state.result) : null),
    [state?.result]
  );

  if (!graph) {
    return (
      <Box sx={{
        position: "relative", height: "calc(100vh - 48px)", overflow: "hidden",
        display: "flex", alignItems: "center", justifyContent: "center",
      }}>
        <Box sx={{ position: "absolute", inset: 0, opacity: 0.35 }}>
          <ParticleBackground density={0.4} linkDistance={140} />
        </Box>
        <Box sx={{ position: "relative", textAlign: "center" }}>
          <Typography sx={{ fontSize: "1.22rem", fontWeight: 600, color: T.textDim, mb: 2 }}>
            No analysis available.
          </Typography>
          <Button variant="contained" onClick={() => navigate("/")}>
            Run an analysis
          </Button>
        </Box>
      </Box>
    );
  }

  return (
    <Box sx={{ position: "relative", height: "calc(100vh - 48px)", overflow: "hidden" }}>
      {/* ambient particles behind the flow canvas */}
      <Box sx={{ position: "absolute", inset: 0, opacity: 0.35 }}>
        <ParticleBackground density={0.4} linkDistance={140} />
      </Box>

      {/* header strip */}
      <Box sx={{
        position: "absolute", top: 0, left: 0, right: 0, zIndex: 20,
        display: "flex", alignItems: "center", gap: 2, px: 3, py: 1.5,
        background: "linear-gradient(rgba(7,11,20,0.9), transparent)",
        pointerEvents: "none",
      }}>
        <Box sx={{ pointerEvents: "auto", display: "flex", alignItems: "center", gap: 2 }}>
          <Button
            startIcon={<ArrowLeft size={17} />}
            onClick={() => navigate("/analysis", { state })}
            sx={{ color: T.textMute, fontSize: "0.96rem", "&:hover": { color: T.text } }}
          >
            Mission Control
          </Button>
          <Typography sx={{ fontSize: "1.09rem", fontWeight: 700, color: T.text }}>
            Enterprise Dependency Graph
          </Typography>
        </Box>

        {/* legend */}
        <Box sx={{ ml: "auto", pointerEvents: "auto", display: "flex", gap: 1.75, flexWrap: "wrap" }}>
          {Object.entries(SYSTEM_HUES).map(([sys, hue]) => (
            <Box key={sys} sx={{ display: "flex", alignItems: "center", gap: 0.6 }}>
              <Box sx={{ width: 9, height: 9, borderRadius: "50%", bgcolor: hue, boxShadow: `0 0 6px ${hue}88` }} />
              <Typography sx={{ fontSize: "0.875rem", color: T.textDim, textTransform: "capitalize" }}>
                {sys}
              </Typography>
            </Box>
          ))}
        </Box>
      </Box>

      {/* the graph — fills everything; toolbar sits below the header strip */}
      <motion.div
        initial={{ opacity: 0, scale: 0.99 }}
        animate={{ opacity: 1, scale: 1 }}
        transition={{ duration: 0.7, ease: T.ease }}
        style={{ position: "absolute", inset: 0, paddingTop: 52 }}
      >
        <Box sx={{ height: "100%", "& > div": { borderRadius: 0, border: "none", boxShadow: "none", background: "transparent" } }}>
          <EnterpriseFlow graph={graph} height="100%" />
        </Box>
      </motion.div>
    </Box>
  );
};

export default DependencyGraph;
