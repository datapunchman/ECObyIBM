/**
 * Home — the ECO command surface.
 *
 * One input, three KPI pulses, ambient enterprise graph, recent analyses.
 * Clicking Analyze launches the cinematic AIThinking sequence and lands on
 * the Mission Control analysis page.
 */
import React, { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Box, Typography, TextField, Button, Chip } from "@mui/material";
import { motion } from "framer-motion";
import { ArrowRight, Sparkles, Clock } from "lucide-react";
import { T } from "@/assets/theme";
import { GlassCard } from "@/components/ui";
import DataEstate from "@/components/DataEstate";
import ParticleBackground from "@/components/ParticleBackground";
import AIThinking from "@/components/AIThinking";
import type { V2AnalysisResult } from "@/types";

const EXAMPLES = [
  "Rename Customer_ID to Client_ID",
  "Remove the DOB column from Customers",
  "Drop the SalesTerritory table",
];

interface RecentEntry {
  request: string;
  at: string;
  assets: number;
  risk: string;
}

function loadRecents(): RecentEntry[] {
  try {
    return JSON.parse(localStorage.getItem("eco:recents") ?? "[]");
  } catch {
    return [];
  }
}

function pushRecent(entry: RecentEntry) {
  const list = [entry, ...loadRecents().filter((r) => r.request !== entry.request)].slice(0, 5);
  try { localStorage.setItem("eco:recents", JSON.stringify(list)); } catch { /* ignore */ }
}

const RISK_COLOR: Record<string, string> = {
  low: T.success, medium: T.amber, high: "#F97316", critical: T.danger,
};

const Home: React.FC = () => {
  const [request, setRequest] = useState("");
  const [thinking, setThinking] = useState(false);
  const [apiDone, setApiDone] = useState(false);
  const [pending, setPending] = useState<{ result: V2AnalysisResult; request: string } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [recents, setRecents] = useState<RecentEntry[]>([]);
  const navigate = useNavigate();

  useEffect(() => setRecents(loadRecents()), []);

  const handleAnalyze = async (text?: string) => {
    const req = (text ?? request).trim();
    if (!req) return;
    setError(null);
    setThinking(true);
    setApiDone(false);
    try {
      const { AnalysisService } = await import("@/services");
      const result = (await AnalysisService.analyzeV2({
        request: req,
        change_type: "unknown",
      })) as unknown as V2AnalysisResult;
      pushRecent({
        request: req,
        at: new Date().toISOString(),
        assets: result.graph_analysis.metrics.total_assets,
        risk: result.llm_summary.risk_level,
      });
      setPending({ result, request: req });
      setApiDone(true); // AIThinking finishes its last stage, then onExited fires
    } catch (e) {
      setThinking(false);
      setError(e instanceof Error ? e.message : "Analysis failed — check that both backend services are running.");
    }
  };

  const handleThinkingDone = () => {
    if (pending) navigate("/analysis", { state: pending });
    setThinking(false);
  };

  return (
    <Box sx={{ position: "relative", minHeight: "calc(100vh - 48px)", overflow: "hidden" }}>
      {/* ambient graph background */}
      <Box sx={{ position: "absolute", inset: 0, opacity: 0.55 }}>
        <ParticleBackground density={0.5} linkDistance={150} />
      </Box>
      {/* animated mesh */}
      <Box
        aria-hidden
        sx={{
          position: "absolute", inset: "-30%",
          background:
            `radial-gradient(38% 45% at 25% 28%, ${T.blue}14 0%, transparent 100%),` +
            `radial-gradient(34% 40% at 76% 60%, ${T.purple}10 0%, transparent 100%),` +
            `radial-gradient(28% 36% at 55% 15%, ${T.cyan}0d 0%, transparent 100%)`,
          backgroundSize: "200% 200%",
          animation: "eco-gradient-pan 32s ease-in-out infinite",
        }}
      />

      <AIThinking open={thinking} done={apiDone} onExited={handleThinkingDone} />

      <Box sx={{ position: "relative", maxWidth: 1160, mx: "auto", px: 3, pt: { xs: 7, md: 11 }, pb: 8 }}>
        <Box sx={{ maxWidth: 860 }}>
        {/* headline */}
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.7, ease: T.ease }}
        >
          <Box sx={{ display: "flex", alignItems: "center", gap: 1, mb: 2.5 }}>
            <Sparkles size={18} color={T.cyan} />
            <Typography sx={{
              fontSize: "0.9rem", fontWeight: 600, letterSpacing: "0.18em",
              textTransform: "uppercase", color: T.cyan,
            }}>
              Enterprise Change Intelligence
            </Typography>
          </Box>
          <Typography sx={{
            fontWeight: 700, fontSize: { xs: "2.44rem", md: "3.54rem" },
            lineHeight: 1.08, letterSpacing: "-0.03em", color: T.text, mb: 4,
          }}>
            See every impact.
            <Box component="span" sx={{
              display: "block",
              background: `linear-gradient(90deg, ${T.blueSoft}, ${T.purple}, ${T.cyan})`,
              backgroundSize: "200% auto",
              WebkitBackgroundClip: "text",
              WebkitTextFillColor: "transparent",
              animation: "eco-gradient-pan 8s ease-in-out infinite",
            }}>
              Before it happens.
            </Box>
          </Typography>
        </motion.div>

        {/* AI input */}
        <motion.div
          initial={{ opacity: 0, y: 22 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.7, delay: 0.12, ease: T.ease }}
        >
          <Box sx={{
            ...T.glass, borderRadius: "18px", p: 0.75,
            transition: "box-shadow 250ms, border-color 250ms",
            "&:focus-within": {
              borderColor: `${T.blue}77`,
              boxShadow: `0 8px 40px rgba(2,6,17,0.6), 0 0 0 3px ${T.blue}22, 0 0 32px ${T.blue}22`,
            },
          }}>
            <Box sx={{ display: "flex", alignItems: "flex-end", gap: 1 }}>
              <TextField
                multiline minRows={1} maxRows={5} fullWidth
                placeholder={`Describe a change — e.g. "${EXAMPLES[0]}"`}
                value={request}
                onChange={(e) => setRequest(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); void handleAnalyze(); }
                }}
                variant="standard"
                InputProps={{ disableUnderline: true }}
                sx={{
                  px: 2, py: 1.25,
                  "& .MuiInputBase-input": {
                    fontFamily: T.mono, fontSize: "1.18rem", color: T.text,
                    "&::placeholder": { color: T.textMute, opacity: 1 },
                  },
                }}
                inputProps={{ "aria-label": "Change request" }}
              />
              <Button
                variant="contained"
                disabled={!request.trim() || thinking}
                onClick={() => void handleAnalyze()}
                endIcon={<ArrowRight size={19} />}
                sx={{ m: 0.75, px: 3, py: 1.1, borderRadius: "12px", whiteSpace: "nowrap" }}
              >
                Analyze
              </Button>
            </Box>
          </Box>

          {error && (
            <Typography sx={{ color: T.danger, fontSize: "1.0rem", mt: 1.5, fontFamily: T.mono }}>
              {error}
            </Typography>
          )}

          {/* examples */}
          <Box sx={{ display: "flex", gap: 1, mt: 2, flexWrap: "wrap" }}>
            {EXAMPLES.map((ex) => (
              <Chip
                key={ex} label={ex} size="small" clickable
                onClick={() => setRequest(ex)}
                sx={{
                  fontSize: "0.9rem", fontFamily: T.mono, color: T.textDim,
                  bgcolor: "rgba(148,163,184,0.06)", border: `1px solid ${T.border}`,
                  "&:hover": { borderColor: `${T.blueSoft}66`, color: T.blueSoft, bgcolor: `${T.blue}11` },
                }}
              />
            ))}
          </Box>
        </motion.div>
        </Box>

        {/* Enterprise Data Estate — the indexed environment (real metadata) */}
        <Box sx={{ mt: 5 }}>
          <DataEstate />
        </Box>

        {/* recent analyses */}
        {recents.length > 0 && (
          <Box sx={{ mt: 5 }}>
            <Box sx={{ display: "flex", alignItems: "center", gap: 1, mb: 1.5 }}>
              <Clock size={16} color={T.textMute as string} />
              <Typography sx={{
                fontSize: "0.875rem", fontWeight: 600, letterSpacing: "0.14em",
                textTransform: "uppercase", color: T.textMute,
              }}>
                Recent analyses
              </Typography>
            </Box>
            <Box sx={{ display: "flex", flexDirection: "column", gap: 1 }}>
              {recents.map((r, i) => (
                <GlassCard
                  key={r.request} delay={i * 0.06}
                  onClick={() => { setRequest(r.request); void handleAnalyze(r.request); }}
                  sx={{ py: 1.5, px: 2, display: "flex", alignItems: "center", gap: 2 }}
                >
                  <Box sx={{
                    width: 8, height: 8, borderRadius: "50%", flexShrink: 0,
                    bgcolor: RISK_COLOR[r.risk] ?? T.textMute,
                    boxShadow: `0 0 8px ${RISK_COLOR[r.risk] ?? "transparent"}`,
                  }} />
                  <Typography sx={{
                    fontFamily: T.mono, fontSize: "1.02rem", color: T.textDim, flex: 1,
                    overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
                  }}>
                    {r.request}
                  </Typography>
                  <Typography sx={{ fontSize: "0.9rem", color: T.textMute, flexShrink: 0 }}>
                    {r.assets} assets
                  </Typography>
                  <ArrowRight size={16} color={T.textMute as string} />
                </GlassCard>
              ))}
            </Box>
          </Box>
        )}
      </Box>
    </Box>
  );
};

export default Home;
