/**
 * LoadingSequence — the "data packet" progress line and the boot log.
 *
 * Not a percentage bar: a hairline track along which packets of light
 * travel, while an init log ticks through the platform subsystems with
 * stroke-drawn checkmarks. The headline types itself on.
 *
 * Calls `onComplete` once the final "Ready" step has landed.
 */
import React, { useEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { ECO_BLUE, ECO_ACCENT } from "./Logo";

const STEPS = [
  "Loading Metadata Engine",
  "Discovering Dependencies",
  "Building Enterprise Graph",
  "Mapping Databases",
  "Loading Power BI Metadata",
  "Connecting AI Reasoning Engine",
  "Initializing Granite",
  "Ready",
] as const;

const HEADLINE = "Initializing Enterprise Intelligence...";
const STEP_INTERVAL_MS = 210;
const MONO = '"IBM Plex Mono", ui-monospace, monospace';

/* ---------------------------------------------------------------- */

const Checkmark: React.FC<{ delay?: number }> = ({ delay = 0 }) => (
  <svg width={13} height={13} viewBox="0 0 14 14" fill="none" aria-hidden>
    <motion.path
      d="M2.5 7.5 L5.5 10.5 L11.5 3.5"
      stroke="#42BE65"
      strokeWidth={1.8}
      strokeLinecap="round"
      strokeLinejoin="round"
      initial={{ pathLength: 0 }}
      animate={{ pathLength: 1 }}
      transition={{ delay, duration: 0.28, ease: "easeOut" }}
    />
  </svg>
);

/* Typewriter for the headline. */
const Typewriter: React.FC<{ text: string; startDelay?: number }> = ({
  text,
  startDelay = 0,
}) => {
  const reduced = useReducedMotion();
  const [count, setCount] = useState(reduced ? text.length : 0);

  useEffect(() => {
    if (reduced) return;
    let i = 0;
    let interval: number | undefined;
    const start = window.setTimeout(() => {
      interval = window.setInterval(() => {
        i += 1;
        setCount(i);
        if (i >= text.length && interval) window.clearInterval(interval);
      }, 26);
    }, startDelay * 1000);
    return () => {
      window.clearTimeout(start);
      if (interval) window.clearInterval(interval);
    };
  }, [text, startDelay, reduced]);

  return (
    <span style={{ fontFamily: MONO, fontSize: 12, letterSpacing: "0.04em", color: "rgba(255,255,255,0.55)" }}>
      {text.slice(0, count)}
      <motion.span
        aria-hidden
        animate={{ opacity: [1, 0] }}
        transition={{ repeat: Infinity, duration: 0.8, repeatType: "reverse" }}
        style={{ color: ECO_ACCENT }}
      >
        ▍
      </motion.span>
    </span>
  );
};

/* ---------------------------------------------------------------- */

export interface LoadingSequenceProps {
  /** seconds after mount before the sequence begins */
  startDelay?: number;
  onComplete?: () => void;
}

const LoadingSequence: React.FC<LoadingSequenceProps> = ({
  startDelay = 0,
  onComplete,
}) => {
  const reduced = useReducedMotion();
  const [begun, setBegun] = useState(false);
  const [stepIndex, setStepIndex] = useState(-1); // index of latest completed step
  const doneRef = useRef(false);

  /* Schedule: begin after startDelay, then tick through the steps. */
  useEffect(() => {
    const t = window.setTimeout(() => setBegun(true), startDelay * 1000);
    return () => window.clearTimeout(t);
  }, [startDelay]);

  useEffect(() => {
    if (!begun) return;
    const interval = window.setInterval(() => {
      setStepIndex((i) => {
        if (i + 1 >= STEPS.length - 1) window.clearInterval(interval);
        return Math.min(i + 1, STEPS.length - 1);
      });
    }, reduced ? 90 : STEP_INTERVAL_MS);
    return () => window.clearInterval(interval);
  }, [begun, reduced]);

  useEffect(() => {
    if (stepIndex === STEPS.length - 1 && !doneRef.current) {
      doneRef.current = true;
      const t = window.setTimeout(() => onComplete?.(), 520);
      return () => window.clearTimeout(t);
    }
  }, [stepIndex, onComplete]);

  /* Only the last three log lines are visible — a scrolling terminal. */
  const visible = useMemo(() => {
    const upTo = STEPS.slice(0, stepIndex + 1);
    return upTo.slice(-3);
  }, [stepIndex]);

  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: begun ? 1 : 0, y: begun ? 0 : 10 }}
      transition={{ duration: 0.5, ease: [0.16, 1, 0.3, 1] }}
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        gap: 18,
        width: 300,
      }}
    >
      {/* ── flowing data-packet line ── */}
      <div
        style={{
          position: "relative",
          width: "100%",
          height: 2,
          borderRadius: 1,
          background: "rgba(255,255,255,0.08)",
          overflow: "hidden",
        }}
        role="progressbar"
        aria-label="Initializing"
      >
        {!reduced &&
          [0, 1, 2].map((i) => (
            <motion.span
              key={i}
              aria-hidden
              style={{
                position: "absolute",
                top: 0,
                left: 0,
                height: "100%",
                width: 64,
                borderRadius: 1,
                background: `linear-gradient(90deg, transparent, ${
                  i === 1 ? ECO_ACCENT : ECO_BLUE
                }, transparent)`,
              }}
              animate={{ x: [-64, 364] }}
              transition={{
                repeat: Infinity,
                duration: 1.5,
                delay: i * 0.5,
                ease: "linear",
              }}
            />
          ))}
        {reduced && (
          <span
            style={{
              position: "absolute",
              inset: 0,
              background: ECO_BLUE,
              opacity: 0.6,
            }}
          />
        )}
      </div>

      {/* ── typed headline ── */}
      <div style={{ height: 16 }}>
        {begun && <Typewriter text={HEADLINE} />}
      </div>

      {/* ── boot log (last three lines) ── */}
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          gap: 7,
          height: 62,
          justifyContent: "flex-end",
          overflow: "hidden",
          width: "100%",
          alignItems: "center",
        }}
        aria-live="polite"
      >
        <AnimatePresence initial={false}>
          {visible.map((label) => {
            const isReady = label === "Ready";
            return (
              <motion.div
                key={label}
                layout
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: isReady ? 1 : 0.55, y: 0 }}
                exit={{ opacity: 0, y: -8 }}
                transition={{ duration: 0.26, ease: "easeOut" }}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 8,
                  fontFamily: MONO,
                  fontSize: 11.5,
                  letterSpacing: "0.03em",
                  color: isReady ? "#FFFFFF" : "rgba(255,255,255,0.72)",
                  fontWeight: isReady ? 500 : 400,
                }}
              >
                <Checkmark delay={0.05} />
                <span>{label}</span>
              </motion.div>
            );
          })}
        </AnimatePresence>
      </div>
    </motion.div>
  );
};

export default LoadingSequence;
