/**
 * Analysis — AI Mission Control.
 *
 * Hero transform (old → new), animated counters, the interactive
 * EnterpriseFlow graph as centrepiece, impact bubbles, risk gauge,
 * deployment/rollback timelines, accordion asset explorer, Power BI
 * report cards, AI summary stat cards, animated validation checklist.
 */
import React, { useEffect, useMemo, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import {
  Box, Typography, Grid, Button, Collapse, TextField, InputAdornment,
} from "@mui/material";
import { motion, AnimatePresence } from "framer-motion";
import {
  ArrowLeft, ArrowDown, Maximize2, Search,
  Database, Layers, BarChart3, NotebookPen, Workflow,
  Brain, AlertTriangle, Clock3, Undo2, Gauge as GaugeIcon,
  ChevronDown, CheckCircle2,
} from "lucide-react";
import { T, SYSTEM_HUES, RISK_HUES } from "@/assets/theme";
import { GlassCard, CountUp, RadialGauge, RiskGauge } from "@/components/ui";
import FlowTimeline from "@/components/FlowTimeline";
import EnterpriseFlow, { buildEcoGraph } from "@/components/EnterpriseFlow";
import type { V2AnalysisResult, V2ImpactedAsset } from "@/types";

interface LocationState {
  result: V2AnalysisResult;
  request: string;
}

/* ── impact bubble groups ─────────────────────────────────────────── */

const BUBBLES: { key: string; label: string; icon: React.ElementType; hue: string; buckets: string[]; systems: string[] }[] = [
  { key: "database", label: "Database", icon: Database, hue: T.purple,
    buckets: ["database_tables", "views", "materialized_views", "stored_procedures", "functions"],
    systems: ["database", "sql"] },
  { key: "semantic", label: "Semantic", icon: Layers, hue: T.cyan,
    buckets: ["semantic_models"], systems: ["powerbi"] },
  { key: "reports", label: "Reports", icon: BarChart3, hue: "#E879F9",
    buckets: ["powerbi_reports", "dashboards"], systems: ["powerbi"] },
  { key: "notebooks", label: "Notebooks", icon: NotebookPen, hue: "#F97316",
    buckets: ["databricks_notebooks", "spark_jobs", "delta_live_tables", "unity_catalog"],
    systems: ["databricks"] },
  { key: "pipelines", label: "Pipelines", icon: Workflow, hue: T.blue,
    buckets: ["pipelines", "data_factory", "airflow", "fabric_pipelines", "adls_files", "apis", "external_consumers"],
    systems: ["pipeline", "api"] },
];

const DEPLOY_SYSTEMS: string[][] = [
  ["database", "sql"], ["databricks"], ["powerbi"], ["pipeline"], [],
];

function bucketAssets(result: V2AnalysisResult, buckets: string[]): V2ImpactedAsset[] {
  const ga = result.graph_analysis as unknown as Record<string, V2ImpactedAsset[]>;
  return buckets.flatMap((b) => ga[b] ?? []);
}

/* ── section label ────────────────────────────────────────────────── */

const SectionLabel: React.FC<{ icon: React.ElementType; children: React.ReactNode }> = ({
  icon: Icon, children,
}) => (
  <Box sx={{ display: "flex", alignItems: "center", gap: 1, mb: 1.75 }}>
    <Icon size={17} color={T.textMute as string} />
    <Typography sx={{
      fontSize: "0.875rem", fontWeight: 700, letterSpacing: "0.16em",
      textTransform: "uppercase", color: T.textMute,
    }}>
      {children}
    </Typography>
  </Box>
);

/* ── main page ────────────────────────────────────────────────────── */

const Analysis: React.FC = () => {
  const location = useLocation();
  const navigate = useNavigate();
  const state = location.state as LocationState | null;

  const [activeBubble, setActiveBubble] = useState<string | null>(null);
  const [openBucket, setOpenBucket] = useState<string | null>(null);
  const [assetSearch, setAssetSearch] = useState("");
  const [graphHighlight, setGraphHighlight] = useState<string[] | null>(null);

  useEffect(() => {
    if (!state?.result) navigate("/");
  }, [state, navigate]);

  const graph = useMemo(
    () => (state?.result ? buildEcoGraph(state.result) : null),
    [state?.result]
  );

  if (!state?.result || !graph) return null;

  const { result } = state;
  const m = result.graph_analysis.metrics;
  const llm = result.llm_summary;
  const cr = result.change_request;
  const riskHue = RISK_HUES[llm.risk_level] ?? T.amber;
  /* Mean of per-asset backend confidence values (graph-discovered assets
     carry confidence=1.0) — not a hardcoded number. */
  const allAssets = BUBBLES.flatMap((b) => bucketAssets(result, b.buckets));
  const confidence = allAssets.length
    ? Math.round((allAssets.reduce((s, a) => s + (a.confidence ?? 1), 0) / allAssets.length) * 100)
    : 100;

  const bubbles = BUBBLES
    .map((b) => ({ ...b, assets: bucketAssets(result, b.buckets) }))
    .filter((b) => b.assets.length > 0);

  const visibleBubbles = activeBubble
    ? bubbles.filter((b) => b.key === activeBubble)
    : bubbles;

  return (
    <Box sx={{ maxWidth: 1440, mx: "auto", px: { xs: 2, md: 4 }, py: 3.5 }}>
      {/* breadcrumb */}
      <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ duration: 0.4 }}>
        <Button
          startIcon={<ArrowLeft size={18} />}
          onClick={() => navigate("/")}
          sx={{ color: T.textMute, fontSize: "1.0rem", mb: 3, "&:hover": { color: T.text } }}
        >
          New analysis
        </Button>
      </motion.div>

      {/* ── HERO: the transform ── */}
      <Grid container spacing={2.5} alignItems="stretch">
        <Grid item xs={12} md={5}>
          <GlassCard hover={false} sx={{ display: "flex", flexDirection: "column", justifyContent: "center", minHeight: 208 }}>
            <Typography sx={{
              fontSize: "0.875rem", fontWeight: 700, letterSpacing: "0.16em",
              textTransform: "uppercase", color: T.textMute, mb: 2,
            }}>
              {cr.change_type.replace(/_/g, " ")}
            </Typography>
            <Typography sx={{
              fontFamily: T.mono, fontWeight: 700, color: T.text,
              fontSize: { xs: "1.92rem", md: "2.43rem" }, lineHeight: 1.1,
              wordBreak: "break-all",
            }}>
              {cr.target_name ?? cr.original_request.slice(0, 32)}
            </Typography>
            {cr.new_name && (
              <>
                <motion.div
                  initial={{ opacity: 0, y: -8 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ delay: 0.5, duration: 0.5, ease: T.ease }}
                  style={{ margin: "10px 0" }}
                >
                  <motion.div
                    animate={{ y: [0, 5, 0] }}
                    transition={{ repeat: Infinity, duration: 2.2, ease: "easeInOut" }}
                    style={{ width: "fit-content" }}
                  >
                    <ArrowDown size={26} color={T.cyan} style={{ filter: `drop-shadow(0 0 8px ${T.cyan})` }} />
                  </motion.div>
                </motion.div>
                <motion.div
                  initial={{ opacity: 0, y: 10 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ delay: 0.75, duration: 0.5, ease: T.ease }}
                >
                  <Typography sx={{
                    fontFamily: T.mono, fontWeight: 700,
                    fontSize: { xs: "1.92rem", md: "2.43rem" }, lineHeight: 1.1,
                    background: `linear-gradient(90deg, ${T.blueSoft}, ${T.cyan})`,
                    WebkitBackgroundClip: "text", WebkitTextFillColor: "transparent",
                    wordBreak: "break-all",
                  }}>
                    {cr.new_name}
                  </Typography>
                </motion.div>
              </>
            )}
          </GlassCard>
        </Grid>

        {/* hero metrics */}
        {([
          { v: m.total_assets, label: "Assets", hue: T.blueSoft },
          { v: m.max_depth, label: "Levels", hue: T.purple },
          { v: confidence, label: "Confidence", hue: T.cyan, suffix: "%" },
          { v: m.critical_assets, label: "Critical", hue: T.danger, pulse: true },
        ] as const).map((s, i) => (
          <Grid item xs={6} md={1.75} key={s.label}>
            <GlassCard delay={0.1 + i * 0.08} sx={{
              display: "flex", flexDirection: "column",
              alignItems: "center", justifyContent: "center", minHeight: 208, position: "relative",
            }}>
              {"pulse" in s && s.pulse && s.v > 0 && (
                <Box sx={{
                  position: "absolute", top: 14, right: 14,
                  width: 9, height: 9, borderRadius: "50%", bgcolor: T.danger,
                  animation: "eco-glow-ring 1.8s ease-out infinite",
                }} />
              )}
              <Typography sx={{
                fontWeight: 700, fontSize: "3.66rem", lineHeight: 1,
                color: s.hue, letterSpacing: "-0.03em",
                textShadow: `0 0 30px ${s.hue}44`,
              }}>
                <CountUp value={s.v} suffix={"suffix" in s ? s.suffix : ""} />
              </Typography>
              <Typography sx={{
                mt: 1.25, fontSize: "0.875rem", fontWeight: 700, letterSpacing: "0.16em",
                textTransform: "uppercase", color: T.textMute,
              }}>
                {s.label}
              </Typography>
            </GlassCard>
          </Grid>
        ))}
      </Grid>

      {/* ── THE GRAPH — hero of the app ── */}
      <Box sx={{ mt: 3 }}>
        <Box sx={{ display: "flex", alignItems: "center", justifyContent: "space-between", mb: 1.75 }}>
          <SectionLabel icon={Brain}>Enterprise impact graph</SectionLabel>
          <Button
            size="small"
            endIcon={<Maximize2 size={16} />}
            onClick={() => navigate("/graph", { state })}
            sx={{ color: T.blueSoft, fontSize: "0.92rem" }}
          >
            Full screen
          </Button>
        </Box>
        <motion.div
          initial={{ opacity: 0, scale: 0.985 }}
          animate={{ opacity: 1, scale: 1 }}
          transition={{ duration: 0.7, delay: 0.2, ease: T.ease }}
        >
          <EnterpriseFlow graph={graph} height="46vh" highlightSystems={graphHighlight} />
        </motion.div>
      </Box>

      {/* ── IMPACT BUBBLES ── */}
      <Box sx={{ mt: 4 }}>
        <SectionLabel icon={GaugeIcon}>Impact by system</SectionLabel>
        <Box sx={{ display: "flex", gap: 2, flexWrap: "wrap", alignItems: "flex-start" }}>
          <AnimatePresence mode="popLayout">
            {visibleBubbles.map((b, i) => {
              const size = Math.min(150, 84 + Math.sqrt(b.assets.length) * 14);
              const Icon = b.icon;
              const expanded = activeBubble === b.key;
              return (
                <motion.div
                  key={b.key}
                  layout
                  initial={{ scale: 0, opacity: 0 }}
                  animate={{ scale: 1, opacity: 1 }}
                  exit={{ scale: 0, opacity: 0 }}
                  transition={{ type: "spring", stiffness: 260, damping: 22, delay: i * 0.06 }}
                >
                  <Box
                    onClick={() => {
                      setActiveBubble(expanded ? null : b.key);
                      setGraphHighlight(expanded ? null : b.systems);
                    }}
                    sx={{
                      width: expanded ? "auto" : size,
                      minWidth: size,
                      height: expanded ? "auto" : size,
                      borderRadius: expanded ? "18px" : "50%",
                      p: expanded ? 2.5 : 0,
                      cursor: "pointer",
                      display: "flex", flexDirection: "column",
                      alignItems: "center", justifyContent: "center",
                      background: `radial-gradient(circle at 32% 28%, ${b.hue}30, rgba(17,24,39,0.85) 72%)`,
                      border: `1px solid ${b.hue}55`,
                      backdropFilter: "blur(14px)",
                      boxShadow: `0 0 28px ${b.hue}22, 0 8px 24px rgba(2,6,17,0.5)`,
                      transition: "border-radius 350ms cubic-bezier(0.16,1,0.3,1)",
                      "&:hover": { borderColor: b.hue, boxShadow: `0 0 36px ${b.hue}44` },
                    }}
                  >
                    <Icon size={expanded ? 16 : 18} color={b.hue} />
                    <Typography sx={{
                      fontWeight: 700, fontSize: expanded ? "1.66rem" : "1.98rem",
                      color: T.text, lineHeight: 1, mt: 0.75,
                    }}>
                      <CountUp value={b.assets.length} duration={1} />
                    </Typography>
                    <Typography sx={{
                      fontSize: "0.875rem", fontWeight: 600, letterSpacing: "0.12em",
                      textTransform: "uppercase", color: T.textDim, mt: 0.5,
                    }}>
                      {b.label}
                    </Typography>
                    {expanded && (
                      <motion.div
                        initial={{ opacity: 0 }}
                        animate={{ opacity: 1 }}
                        transition={{ delay: 0.2 }}
                        style={{ marginTop: 14, display: "flex", flexWrap: "wrap", gap: 6, maxWidth: 420, justifyContent: "center" }}
                      >
                        {b.assets.slice(0, 24).map((a) => (
                          <Box key={a.id} sx={{
                            px: 1, py: 0.4, borderRadius: "6px",
                            border: `1px solid ${b.hue}44`, bgcolor: `${b.hue}11`,
                          }}>
                            <Typography sx={{ fontFamily: T.mono, fontSize: "0.875rem", color: T.textDim }}>
                              {a.asset}
                            </Typography>
                          </Box>
                        ))}
                        {b.assets.length > 24 && (
                          <Typography sx={{ fontSize: "0.875rem", color: T.textMute, alignSelf: "center" }}>
                            +{b.assets.length - 24} more
                          </Typography>
                        )}
                      </motion.div>
                    )}
                  </Box>
                </motion.div>
              );
            })}
          </AnimatePresence>
        </Box>
      </Box>

      {/* ── RISK + AI SUMMARY + DEPLOYMENT row ── */}
      <Grid container spacing={2.5} sx={{ mt: 2 }}>
        {/* risk gauge */}
        <Grid item xs={12} md={4}>
          <GlassCard hover={false} sx={{ height: "100%" }}>
            <SectionLabel icon={AlertTriangle}>Risk assessment</SectionLabel>
            <RiskGauge level={llm.risk_level} />
            <Box sx={{ display: "flex", alignItems: "center", gap: 2, mt: 2, justifyContent: "center" }}>
              <RadialGauge fraction={confidence / 100} size={64} stroke={5} color={T.cyan}>
                <Typography sx={{ fontSize: "1.05rem", fontWeight: 700, color: T.text }}>
                  {confidence}%
                </Typography>
              </RadialGauge>
              <Box>
                <Typography sx={{ fontSize: "0.875rem", color: T.textMute, textTransform: "uppercase", letterSpacing: "0.1em", fontWeight: 700 }}>
                  Confidence
                </Typography>
                <Typography sx={{ fontSize: "0.92rem", color: T.textDim, mt: 0.25 }}>
                  graph-grounded
                </Typography>
              </Box>
            </Box>
          </GlassCard>
        </Grid>

        {/* AI summary cards — every value is a backend field (metrics or
            llm_summary); invented estimates (migration time, rollback
            complexity, "highest risk") removed */}
        <Grid item xs={12} md={4}>
          <SectionLabel icon={Brain}>AI detected</SectionLabel>
          <Box sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}>
            {([
              { icon: Layers, label: "Downstream assets", value: `${m.total_assets}`, hue: T.blueSoft },
              { icon: AlertTriangle, label: "Critical assets", value: `${m.critical_assets}`, hue: T.danger },
              { icon: Clock3, label: "Max depth", value: `${m.max_depth} levels`, hue: T.cyan },
              { icon: Undo2, label: "Risk level", value: llm.risk_level, hue: T.purple },
            ] as const).map((c, i) => {
              const Icon = c.icon;
              return (
                <GlassCard key={c.label} delay={i * 0.07} sx={{ py: 1.5, px: 2, display: "flex", alignItems: "center", gap: 1.75 }}>
                  <Box sx={{
                    width: 34, height: 34, borderRadius: "9px", flexShrink: 0,
                    display: "flex", alignItems: "center", justifyContent: "center",
                    bgcolor: `${c.hue}18`, border: `1px solid ${c.hue}33`,
                  }}>
                    <Icon size={18} color={c.hue} />
                  </Box>
                  <Box sx={{ minWidth: 0 }}>
                    <Typography sx={{ fontSize: "0.875rem", color: T.textMute, textTransform: "uppercase", letterSpacing: "0.1em", fontWeight: 700 }}>
                      {c.label}
                    </Typography>
                    <Typography sx={{
                      fontSize: "1.18rem", fontWeight: 700, color: T.text,
                      overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
                    }}>
                      {c.value}
                    </Typography>
                  </Box>
                </GlassCard>
              );
            })}
          </Box>
        </Grid>

        {/* deployment timeline */}
        <Grid item xs={12} md={4}>
          <GlassCard hover={false} sx={{ height: "100%" }}>
            <SectionLabel icon={Workflow}>Deployment sequence</SectionLabel>
            <FlowTimeline
              steps={llm.deployment_plan}
              color={T.blue}
              onHoverStep={(i) =>
                setGraphHighlight(i === null ? null : DEPLOY_SYSTEMS[Math.min(i, DEPLOY_SYSTEMS.length - 1)])
              }
            />
          </GlassCard>
        </Grid>
      </Grid>

      {/* ── ASSET EXPLORER + REPORT CARDS + VALIDATION/ROLLBACK ── */}
      <Grid container spacing={2.5} sx={{ mt: 1 }}>
        {/* accordion asset explorer */}
        <Grid item xs={12} md={5}>
          <GlassCard hover={false}>
            <SectionLabel icon={Database}>Impacted assets</SectionLabel>
            <TextField
              size="small" fullWidth placeholder="Filter assets…"
              value={assetSearch}
              onChange={(e) => setAssetSearch(e.target.value)}
              InputProps={{
                startAdornment: (
                  <InputAdornment position="start">
                    <Search size={16} color={T.textMute as string} />
                  </InputAdornment>
                ),
              }}
              sx={{ mb: 1.5, "& .MuiOutlinedInput-root": { fontSize: "1.0rem", borderRadius: "9px" } }}
            />
            {bubbles.map((b) => {
              const filtered = b.assets.filter((a) =>
                a.asset.toLowerCase().includes(assetSearch.toLowerCase())
              );
              if (!filtered.length) return null;
              const open = openBucket === b.key;
              return (
                <Box key={b.key} sx={{ mb: 0.75 }}>
                  <Box
                    onClick={() => setOpenBucket(open ? null : b.key)}
                    sx={{
                      display: "flex", alignItems: "center", gap: 1.25, px: 1.5, py: 1.1,
                      borderRadius: "10px", cursor: "pointer",
                      border: `1px solid ${open ? `${b.hue}44` : "transparent"}`,
                      bgcolor: open ? `${b.hue}0d` : "transparent",
                      transition: "all 180ms",
                      "&:hover": { bgcolor: `${b.hue}11` },
                    }}
                  >
                    <Box sx={{ width: 8, height: 8, borderRadius: "50%", bgcolor: b.hue, boxShadow: `0 0 8px ${b.hue}88` }} />
                    <Typography sx={{ fontSize: "1.02rem", fontWeight: 600, color: T.text, flex: 1 }}>
                      {b.label}
                    </Typography>
                    <Typography sx={{ fontSize: "0.92rem", fontWeight: 700, color: b.hue }}>
                      {filtered.length}
                    </Typography>
                    <motion.div animate={{ rotate: open ? 180 : 0 }}>
                      <ChevronDown size={17} color={T.textMute as string} />
                    </motion.div>
                  </Box>
                  <Collapse in={open}>
                    <Box sx={{ pl: 3, pr: 1, py: 0.75, maxHeight: 220, overflowY: "auto" }}>
                      {filtered.map((a, ai) => (
                        <motion.div
                          key={a.id}
                          initial={{ opacity: 0, x: -8 }}
                          animate={{ opacity: 1, x: 0 }}
                          transition={{ delay: ai * 0.025, duration: 0.25 }}
                        >
                          <Box sx={{
                            display: "flex", alignItems: "center", gap: 1, py: 0.6,
                            borderBottom: `1px solid ${T.border}`,
                          }}>
                            <Typography sx={{
                              fontFamily: T.mono, fontSize: "0.92rem", color: T.textDim, flex: 1,
                              overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
                            }}>
                              {a.asset}
                            </Typography>
                            {/* type verbatim from backend — no invented risk badge */}
                            <Typography sx={{
                              fontSize: "0.875rem", px: 0.75, py: 0.2, borderRadius: "5px",
                              bgcolor: `${b.hue}1a`, color: b.hue,
                              fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.06em",
                            }}>
                              {a.type.replace(/_/g, " ")}
                            </Typography>
                            <Typography sx={{ fontSize: "0.875rem", color: T.textMute, width: 76, textAlign: "right" }}>
                              {a.system}
                            </Typography>
                          </Box>
                        </motion.div>
                      ))}
                    </Box>
                  </Collapse>
                </Box>
              );
            })}
          </GlassCard>
        </Grid>

        {/* right stack: report cards + validation + rollback */}
        <Grid item xs={12} md={7}>
          {/* Power BI report cards */}
          {bubbles.some((b) => b.key === "reports") && (
            <>
              <SectionLabel icon={BarChart3}>Power BI surface</SectionLabel>
              <Grid container spacing={1.5} sx={{ mb: 3 }}>
                {bucketAssets(result, ["powerbi_reports", "dashboards"]).slice(0, 6).map((r, i) => {
                  /* badge shows the backend asset_type — index-based fake
                     severity (high/medium/low) removed */
                  const hue = SYSTEM_HUES.powerbi;
                  return (
                    <Grid item xs={12} sm={6} md={4} key={r.id}>
                      <GlassCard delay={i * 0.06} sx={{ p: 2 }}>
                        <Box sx={{ display: "flex", alignItems: "center", justifyContent: "space-between", mb: 1 }}>
                          <BarChart3 size={18} color={SYSTEM_HUES.powerbi} />
                          <Box sx={{
                            px: 0.9, py: 0.25, borderRadius: "6px",
                            bgcolor: `${hue}1c`, border: `1px solid ${hue}44`,
                          }}>
                            <Typography sx={{
                              fontSize: "0.875rem", fontWeight: 700, color: hue,
                              textTransform: "uppercase", letterSpacing: "0.08em",
                            }}>
                              {r.type.replace(/_/g, " ")}
                            </Typography>
                          </Box>
                        </Box>
                        <Typography sx={{
                          fontSize: "1.08rem", fontWeight: 700, color: T.text,
                          overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
                        }}>
                          {r.asset}
                        </Typography>
                        <Typography sx={{ fontSize: "0.875rem", color: T.textMute, mt: 0.4 }}>
                          Affected · {r.system}
                        </Typography>
                      </GlassCard>
                    </Grid>
                  );
                })}
              </Grid>
            </>
          )}

          <Grid container spacing={2.5}>
            {/* validation checklist */}
            <Grid item xs={12} sm={6}>
              <GlassCard hover={false} sx={{ height: "100%" }}>
                <SectionLabel icon={CheckCircle2}>Validation</SectionLabel>
                <Box sx={{ display: "flex", flexDirection: "column", gap: 1.1 }}>
                  {llm.validation_checklist.map((check, i) => (
                    <motion.div
                      key={i}
                      initial={{ opacity: 0, x: -10 }}
                      whileInView={{ opacity: 1, x: 0 }}
                      viewport={{ once: true }}
                      transition={{ delay: i * 0.09, duration: 0.35, ease: T.ease }}
                    >
                      <Box
                        title={check}
                        sx={{
                          display: "flex", alignItems: "flex-start", gap: 1.25,
                          "&:hover .eco-check-circle": { borderColor: T.success, boxShadow: `0 0 10px ${T.success}44` },
                        }}
                      >
                        <Box className="eco-check-circle" sx={{
                          width: 17, height: 17, borderRadius: "50%", flexShrink: 0, mt: 0.2,
                          border: `1.5px solid rgba(148,163,184,0.3)`,
                          display: "flex", alignItems: "center", justifyContent: "center",
                          transition: "all 200ms",
                        }}>
                          <motion.svg width={9} height={9} viewBox="0 0 14 14" fill="none">
                            <motion.path
                              d="M2.5 7.5 L5.5 10.5 L11.5 3.5"
                              stroke={T.success} strokeWidth={2.2} strokeLinecap="round" strokeLinejoin="round"
                              initial={{ pathLength: 0 }}
                              whileInView={{ pathLength: 1 }}
                              viewport={{ once: true }}
                              transition={{ delay: 0.3 + i * 0.09, duration: 0.3 }}
                            />
                          </motion.svg>
                        </Box>
                        <Typography sx={{
                          fontSize: "0.95rem", color: T.textDim, lineHeight: 1.45,
                          display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical",
                          overflow: "hidden",
                        }}>
                          {check}
                        </Typography>
                      </Box>
                    </motion.div>
                  ))}
                </Box>
              </GlassCard>
            </Grid>

            {/* rollback timeline */}
            <Grid item xs={12} sm={6}>
              <GlassCard hover={false} sx={{ height: "100%" }}>
                <SectionLabel icon={Undo2}>Rollback</SectionLabel>
                <FlowTimeline steps={llm.rollback_plan} color={T.blueSoft} />
              </GlassCard>
            </Grid>
          </Grid>
        </Grid>
      </Grid>

      {/* risk rationale — one line, no paragraph wall */}
      <motion.div
        initial={{ opacity: 0 }}
        whileInView={{ opacity: 1 }}
        viewport={{ once: true }}
        transition={{ duration: 0.6 }}
      >
        <Box sx={{
          mt: 3, px: 2.5, py: 1.75, borderRadius: "12px",
          borderLeft: `3px solid ${riskHue}`,
          background: `linear-gradient(90deg, ${riskHue}0d, transparent)`,
        }}>
          <Typography sx={{ fontSize: "1.0rem", color: T.textDim, fontStyle: "italic" }}>
            {llm.risk_rationale.split(".").slice(0, 2).join(".") + "."}
          </Typography>
        </Box>
      </motion.div>
    </Box>
  );
};

export default Analysis;
