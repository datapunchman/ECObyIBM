/**
 * AIThinking — cinematic full-screen sequence shown while /analyze/v2 runs.
 *
 * Not a spinner: a graph builds itself in the background while pipeline
 * stages tick through with drawn checkmarks and connecting lines. The
 * sequence loops its final stage until `done` flips true, then calls
 * `onExited` after the outro.
 */
import React, { useEffect, useMemo, useState } from "react";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { Box, Typography } from "@mui/material";
import { T } from "@/assets/theme";
import ParticleBackground from "./ParticleBackground";

const STAGES = [
  "Reading Semantic Model",
  "Discovering Dependencies",
  "Building Enterprise Graph",
  "Detecting Downstream Assets",
  "Running IBM Granite",
  "Generating Deployment Strategy",
] as const;

/** Stage nodes for the self-building mini graph (percent coords). */
const STAGE_NODES: ReadonlyArray<readonly [number, number]> = [
  [12, 30], [30, 62], [46, 26], [62, 58], [78, 32], [90, 60],
];

const Check: React.FC<{ active: boolean }> = ({ active }) => (
  <Box
    sx={{
      width: 22, height: 22, borderRadius: "50%",
      border: `1.5px solid ${active ? T.success : "rgba(148,163,184,0.25)"}`,
      display: "flex", alignItems: "center", justifyContent: "center",
      flexShrink: 0, transition: "border-color 250ms",
      ...(active && { boxShadow: `0 0 12px ${T.success}44` }),
    }}
  >
    <svg width={11} height={11} viewBox="0 0 14 14" fill="none">
      {active && (
        <motion.path
          d="M2.5 7.5 L5.5 10.5 L11.5 3.5"
          stroke={T.success}
          strokeWidth={2}
          strokeLinecap="round"
          strokeLinejoin="round"
          initial={{ pathLength: 0 }}
          animate={{ pathLength: 1 }}
          transition={{ duration: 0.3, ease: "easeOut" }}
        />
      )}
    </svg>
  </Box>
);

export interface AIThinkingProps {
  open: boolean;
  /** true when the API response has arrived — lets the sequence finish */
  done: boolean;
  onExited?: () => void;
}

const STAGE_MS = 750;

const AIThinking: React.FC<AIThinkingProps> = ({ open, done, onExited }) => {
  const reduced = useReducedMotion();
  const [stage, setStage] = useState(0);

  useEffect(() => {
    if (!open) { setStage(0); return; }
    const iv = window.setInterval(() => {
      setStage((s) => {
        // hold at the penultimate stage until the API is done
        const cap = done ? STAGES.length : STAGES.length - 1;
        return Math.min(s + 1, cap);
      });
    }, reduced ? 220 : STAGE_MS);
    return () => window.clearInterval(iv);
  }, [open, done, reduced]);

  const finished = stage >= STAGES.length && done;

  useEffect(() => {
    if (finished) {
      const t = window.setTimeout(() => onExited?.(), 700);
      return () => window.clearTimeout(t);
    }
  }, [finished, onExited]);

  const edges = useMemo(
    () =>
      STAGE_NODES.slice(0, -1).map((p, i) => ({
        from: p,
        to: STAGE_NODES[i + 1],
        idx: i,
      })),
    []
  );

  return (
    <AnimatePresence>
      {open && !finished && (
        <motion.div
          key="ai-thinking"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0, scale: 1.04 }}
          transition={{ duration: 0.5, ease: "easeInOut" }}
          style={{
            position: "fixed", inset: 0, zIndex: 1800,
            background: "rgba(7,11,20,0.92)",
            backdropFilter: "blur(20px)",
            display: "flex", alignItems: "center", justifyContent: "center",
          }}
        >
          <Box sx={{ position: "absolute", inset: 0, opacity: 0.5 }}>
            <ParticleBackground density={0.4} linkDistance={130} />
          </Box>

          <Box sx={{ position: "relative", width: "min(560px, 90vw)" }}>
            {/* ── self-building graph ── */}
            <Box sx={{ position: "relative", height: 130, mb: 4 }}>
              <svg width="100%" height="100%" viewBox="0 0 100 80" preserveAspectRatio="none">
                {edges.map(({ from, to, idx }) => (
                  <motion.line
                    key={idx}
                    x1={from[0]} y1={from[1] * 0.8}
                    x2={to[0]} y2={to[1] * 0.8}
                    stroke={idx < stage ? T.blue : "rgba(148,163,184,0.15)"}
                    strokeWidth={0.7}
                    initial={{ pathLength: 0 }}
                    animate={{ pathLength: idx < stage ? 1 : 0 }}
                    transition={{ duration: 0.5, ease: "easeInOut" }}
                    style={idx < stage ? { filter: `drop-shadow(0 0 2px ${T.blue})` } : undefined}
                  />
                ))}
              </svg>
              {STAGE_NODES.map(([x, y], i) => (
                <motion.div
                  key={i}
                  initial={{ scale: 0, opacity: 0 }}
                  animate={{
                    scale: i <= stage ? 1 : 0.4,
                    opacity: i <= stage ? 1 : 0.25,
                  }}
                  transition={{ type: "spring", stiffness: 420, damping: 20 }}
                  style={{
                    position: "absolute",
                    left: `${x}%`, top: `${y}%`,
                    width: 10, height: 10, borderRadius: "50%",
                    background: i <= stage ? T.blueSoft : "rgba(148,163,184,0.3)",
                    boxShadow: i <= stage ? `0 0 14px ${T.blue}` : "none",
                    transform: "translate(-50%, -50%)",
                  }}
                />
              ))}
            </Box>

            {/* ── stage checklist ── */}
            <Box sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}>
              {STAGES.map((label, i) => {
                const isDone = i < stage;
                const isActive = i === stage;
                return (
                  <motion.div
                    key={label}
                    initial={{ opacity: 0, x: -14 }}
                    animate={{ opacity: isDone || isActive ? 1 : 0.28, x: 0 }}
                    transition={{ delay: i * 0.06, duration: 0.4, ease: T.ease }}
                    style={{ display: "flex", alignItems: "center", gap: 14 }}
                  >
                    <Check active={isDone} />
                    <Typography
                      sx={{
                        fontFamily: T.mono,
                        fontSize: "1.09rem",
                        color: isDone ? T.text : isActive ? T.blueSoft : T.textMute,
                        letterSpacing: "0.02em",
                      }}
                    >
                      {label}
                      {isActive && (
                        <motion.span
                          animate={{ opacity: [1, 0.2] }}
                          transition={{ repeat: Infinity, duration: 0.7, repeatType: "reverse" }}
                          style={{ color: T.cyan }}
                        >
                          {" ▍"}
                        </motion.span>
                      )}
                    </Typography>
                  </motion.div>
                );
              })}
            </Box>
          </Box>
        </motion.div>
      )}
    </AnimatePresence>
  );
};

export default AIThinking;
