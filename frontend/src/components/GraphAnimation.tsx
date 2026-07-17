/**
 * GraphAnimation — the splash centrepiece.
 *
 * The ECO "Impact Lattice" mark assembles like an enterprise dependency
 * graph being constructed (compressed ~1.6s schedule):
 *
 *   0.15  lattice nodes pop in, scattered slightly off-anchor
 *   0.45  glowing struts draw between them
 *   0.95  impact node lands with a spring + accent glow
 *   1.30  one soft glow pulse behind the finished mark
 *
 * All timing is driven by absolute delays so the component is a pure
 * function of mount time — the parent (SplashScreen) owns the schedule.
 */
import React from "react";
import { motion, useReducedMotion } from "framer-motion";
import {
  ECO_ARC_PATH,
  ECO_BARS,
  ECO_NODES,
  ECO_NODE_R,
  ECO_STROKE,
  ECO_VIEWBOX,
  ECO_BLUE,
  ECO_ACCENT,
  ECO_IMPACT_NODE,
} from "./Logo";

const EASE_OUT: [number, number, number, number] = [0.16, 1, 0.3, 1];

/* Scatter offsets per node — where each anchor flies in from. */
const SCATTER: ReadonlyArray<readonly [number, number]> = [
  [-9, -7], [-11, 3], [-8, 9], [7, -10], [9, 8], [11, -2],
];

export interface GraphAnimationProps {
  size?: number;
}

const GraphAnimation: React.FC<GraphAnimationProps> = ({ size = 168 }) => {
  const reduced = useReducedMotion();

  if (reduced) {
    return (
      <motion.svg
        width={size}
        height={size}
        viewBox={`0 0 ${ECO_VIEWBOX} ${ECO_VIEWBOX}`}
        fill="none"
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ duration: 0.5 }}
        aria-hidden
      >
        <path d={ECO_ARC_PATH} stroke={ECO_BLUE} strokeWidth={ECO_STROKE} strokeLinecap="round" />
        {ECO_BARS.map(([x1, y1, x2, y2], i) => (
          <line key={i} x1={x1} y1={y1} x2={x2} y2={y2} stroke={ECO_BLUE} strokeWidth={ECO_STROKE} strokeLinecap="round" />
        ))}
        {ECO_NODES.map(([cx, cy], i) => (
          <circle
            key={i} cx={cx} cy={cy}
            r={i === ECO_IMPACT_NODE ? ECO_NODE_R + 1 : ECO_NODE_R}
            fill={i === ECO_IMPACT_NODE ? ECO_ACCENT : ECO_BLUE}
          />
        ))}
      </motion.svg>
    );
  }

  return (
    <motion.div
      style={{ width: size, height: size, position: "relative" }}
      /* settle: slight scale-up with a 2° tilt that relaxes back */
      initial={{ scale: 1, rotate: 0 }}
      animate={{ scale: [1, 1, 1.05, 1.02], rotate: [0, 0, 2, 0] }}
      transition={{ duration: 1.8, times: [0, 0.62, 0.78, 1], ease: EASE_OUT }}
    >
      {/* soft glow pulse behind the finished mark */}
      <motion.div
        aria-hidden
        style={{
          position: "absolute",
          inset: "-30%",
          borderRadius: "50%",
          background:
            "radial-gradient(circle, rgba(15,98,254,0.28) 0%, rgba(15,98,254,0) 62%)",
          filter: "blur(6px)",
        }}
        initial={{ opacity: 0, scale: 0.7 }}
        animate={{ opacity: [0, 0.9, 0.25], scale: [0.7, 1.15, 1.25] }}
        transition={{ delay: 1.3, duration: 1.0, ease: "easeOut" }}
      />

      <svg
        width={size}
        height={size}
        viewBox={`0 0 ${ECO_VIEWBOX} ${ECO_VIEWBOX}`}
        fill="none"
        style={{ position: "relative" }}
        aria-hidden
      >
        {/* ── struts draw like a graph wiring itself (0.45s) ── */}
        <motion.path
          d={ECO_ARC_PATH}
          stroke={ECO_BLUE}
          strokeWidth={ECO_STROKE}
          strokeLinecap="round"
          initial={{ pathLength: 0, opacity: 0 }}
          animate={{ pathLength: 1, opacity: 1 }}
          transition={{ delay: 0.45, duration: 0.4, ease: EASE_OUT }}
          style={{ filter: `drop-shadow(0 0 3px ${ECO_BLUE}66)` }}
        />
        {ECO_BARS.map(([x1, y1, x2, y2], i) => (
          <motion.line
            key={`b${i}`}
            x1={x1} y1={y1} x2={x2} y2={y2}
            stroke={ECO_BLUE}
            strokeWidth={ECO_STROKE}
            strokeLinecap="round"
            initial={{ pathLength: 0, opacity: 0 }}
            animate={{ pathLength: 1, opacity: 1 }}
            transition={{ delay: 0.55 + i * 0.09, duration: 0.38, ease: EASE_OUT }}
            style={{ filter: `drop-shadow(0 0 3px ${ECO_BLUE}55)` }}
          />
        ))}

        {/* ── nodes fly in from scatter, impact node lands last ── */}
        {ECO_NODES.map(([cx, cy], i) => {
          const [dx, dy] = SCATTER[i];
          const isImpact = i === ECO_IMPACT_NODE;
          return (
            <motion.circle
              key={`n${i}`}
              r={isImpact ? ECO_NODE_R + 1 : ECO_NODE_R}
              fill={isImpact ? ECO_ACCENT : ECO_BLUE}
              initial={{ cx: cx + dx, cy: cy + dy, opacity: 0, scale: 0 }}
              animate={{ cx, cy, opacity: 1, scale: 1 }}
              transition={{
                delay: isImpact ? 0.95 : 0.15 + i * 0.06,
                type: "spring",
                stiffness: 380,
                damping: 22,
              }}
              style={isImpact ? { filter: `drop-shadow(0 0 6px ${ECO_ACCENT})` } : undefined}
            />
          );
        })}
      </svg>
    </motion.div>
  );
};

export default GraphAnimation;
