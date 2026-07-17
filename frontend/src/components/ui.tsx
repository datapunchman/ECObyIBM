/**
 * ui.tsx — ECO animated UI primitives.
 *
 * GlassCard     — glass surface with hover lift
 * CountUp       — number that counts up on first view
 * RadialGauge   — circular progress ring with centred value
 * RiskGauge     — semicircular needle gauge (low/medium/high/critical)
 * PulseDot      — glowing status dot
 * KpiCard       — huge-number stat tile with animated visual
 */
import React, { useEffect, useRef, useState } from "react";
import { Box, Typography } from "@mui/material";
import { motion, useInView, useReducedMotion } from "framer-motion";
import { T, RISK_HUES } from "@/assets/theme";

/* ────────────────────────────────────────────────────────────────── */
/* GlassCard                                                          */
/* ────────────────────────────────────────────────────────────────── */

export const GlassCard: React.FC<{
  children: React.ReactNode;
  sx?: object;
  hover?: boolean;
  onClick?: () => void;
  delay?: number;
}> = ({ children, sx, hover = true, onClick, delay = 0 }) => (
  <motion.div
    initial={{ opacity: 0, y: 16 }}
    whileInView={{ opacity: 1, y: 0 }}
    viewport={{ once: true, margin: "-40px" }}
    transition={{ duration: 0.55, delay, ease: T.ease }}
    whileHover={hover ? { y: -3, transition: { duration: 0.2 } } : undefined}
    onClick={onClick}
    style={{ cursor: onClick ? "pointer" : undefined, height: "100%" }}
  >
    <Box
      sx={{
        ...T.glass,
        borderRadius: "16px",
        p: 2.5,
        height: "100%",
        transition: "border-color 200ms, box-shadow 200ms",
        ...(hover && {
          "&:hover": {
            borderColor: "rgba(77,163,255,0.30)",
            boxShadow: "0 12px 40px rgba(2,6,17,0.65), 0 0 24px rgba(15,98,254,0.10), inset 0 1px 0 rgba(255,255,255,0.05)",
          },
        }),
        ...sx,
      }}
    >
      {children}
    </Box>
  </motion.div>
);

/* ────────────────────────────────────────────────────────────────── */
/* CountUp                                                            */
/* ────────────────────────────────────────────────────────────────── */

export const CountUp: React.FC<{
  value: number;
  duration?: number;
  suffix?: string;
  style?: React.CSSProperties;
}> = ({ value, duration = 1.4, suffix = "", style }) => {
  const ref = useRef<HTMLSpanElement>(null);
  const inView = useInView(ref, { once: true });
  const reduced = useReducedMotion();
  const [display, setDisplay] = useState(0);

  useEffect(() => {
    if (!inView) return;
    if (reduced || value === 0) {
      setDisplay(value);
      return;
    }
    let raf = 0;
    const t0 = performance.now();
    const tick = (t: number) => {
      const p = Math.min((t - t0) / (duration * 1000), 1);
      // easeOutExpo
      const eased = p === 1 ? 1 : 1 - Math.pow(2, -10 * p);
      setDisplay(Math.round(eased * value));
      if (p < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [inView, value, duration, reduced]);

  return (
    <span ref={ref} style={{ fontVariantNumeric: "tabular-nums", ...style }}>
      {display.toLocaleString()}
      {suffix}
    </span>
  );
};

/* ────────────────────────────────────────────────────────────────── */
/* RadialGauge — circular progress                                    */
/* ────────────────────────────────────────────────────────────────── */

export const RadialGauge: React.FC<{
  /** 0..1 */
  fraction: number;
  size?: number;
  stroke?: number;
  color?: string;
  children?: React.ReactNode;
}> = ({ fraction, size = 88, stroke = 7, color = T.cyan, children }) => {
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  return (
    <Box sx={{ position: "relative", width: size, height: size, flexShrink: 0 }}>
      <svg width={size} height={size} style={{ transform: "rotate(-90deg)" }}>
        <circle
          cx={size / 2} cy={size / 2} r={r}
          fill="none" stroke="rgba(148,163,184,0.12)" strokeWidth={stroke}
        />
        <motion.circle
          cx={size / 2} cy={size / 2} r={r}
          fill="none" stroke={color} strokeWidth={stroke} strokeLinecap="round"
          strokeDasharray={c}
          initial={{ strokeDashoffset: c }}
          whileInView={{ strokeDashoffset: c * (1 - Math.max(0.02, fraction)) }}
          viewport={{ once: true }}
          transition={{ duration: 1.3, ease: T.ease, delay: 0.2 }}
          style={{ filter: `drop-shadow(0 0 6px ${color}66)` }}
        />
      </svg>
      <Box
        sx={{
          position: "absolute", inset: 0,
          display: "flex", alignItems: "center", justifyContent: "center",
          flexDirection: "column",
        }}
      >
        {children}
      </Box>
    </Box>
  );
};

/* ────────────────────────────────────────────────────────────────── */
/* RiskGauge — semicircular needle                                    */
/* ────────────────────────────────────────────────────────────────── */

const RISK_ANGLE: Record<string, number> = {
  low: -62, medium: -20, high: 24, critical: 66,
};

export const RiskGauge: React.FC<{
  level: string;
  size?: number;
}> = ({ level, size = 210 }) => {
  const color = RISK_HUES[level] ?? T.amber;
  const angle = RISK_ANGLE[level] ?? 0;
  const w = size;
  const h = size * 0.62;
  const cx = w / 2;
  const cy = h * 0.92;
  const r = w * 0.40;

  const arc = (start: number, end: number) => {
    const s = ((start - 90) * Math.PI) / 180;
    const e = ((end - 90) * Math.PI) / 180;
    return `M ${cx + r * Math.sin(s + Math.PI)} ${cy + r * Math.cos(s + Math.PI) * -1 + 0}
            A ${r} ${r} 0 0 1 ${cx + r * Math.sin(e + Math.PI)} ${cy + r * Math.cos(e + Math.PI) * -1}`;
  };
  // simpler: four segment arcs by angle ranges (-80..80)
  const seg = (a0: number, a1: number, colorSeg: string, active: boolean) => {
    const p0 = polar(cx, cy, r, a0);
    const p1 = polar(cx, cy, r, a1);
    return (
      <path
        key={`${a0}`}
        d={`M ${p0.x} ${p0.y} A ${r} ${r} 0 0 1 ${p1.x} ${p1.y}`}
        stroke={colorSeg}
        strokeOpacity={active ? 1 : 0.22}
        strokeWidth={9}
        strokeLinecap="round"
        fill="none"
        style={active ? { filter: `drop-shadow(0 0 8px ${colorSeg}88)` } : undefined}
      />
    );
  };
  void arc;

  return (
    <Box sx={{ position: "relative", width: w, height: h + 34, mx: "auto" }}>
      <svg width={w} height={h + 10}>
        {seg(-80, -44, RISK_HUES.low, level === "low")}
        {seg(-38, -2, RISK_HUES.medium, level === "medium")}
        {seg(4, 40, RISK_HUES.high, level === "high")}
        {seg(46, 80, RISK_HUES.critical, level === "critical")}
        {/* needle */}
        <motion.g
          initial={{ rotate: -80 }}
          whileInView={{ rotate: angle }}
          viewport={{ once: true }}
          transition={{ type: "spring", stiffness: 60, damping: 12, delay: 0.4 }}
          style={{ originX: `${cx}px`, originY: `${cy}px` }}
        >
          <line
            x1={cx} y1={cy} x2={cx} y2={cy - r + 16}
            stroke={T.text} strokeWidth={2.5} strokeLinecap="round"
          />
          <circle cx={cx} cy={cy - r + 12} r={3.5} fill={color}
            style={{ filter: `drop-shadow(0 0 6px ${color})` }} />
        </motion.g>
        <circle cx={cx} cy={cy} r={7} fill={T.card} stroke={color} strokeWidth={2} />
      </svg>
      <Box sx={{ textAlign: "center", mt: -1.5 }}>
        <motion.div
          initial={{ opacity: 0, scale: 0.8 }}
          whileInView={{ opacity: 1, scale: 1 }}
          viewport={{ once: true }}
          transition={{ delay: 0.9, duration: 0.4, ease: T.ease }}
        >
          <Typography
            sx={{
              fontWeight: 700, fontSize: "1.47rem", letterSpacing: "0.16em",
              color, textTransform: "uppercase",
              textShadow: `0 0 18px ${color}66`,
            }}
          >
            {level}
          </Typography>
        </motion.div>
      </Box>
    </Box>
  );
};

function polar(cx: number, cy: number, r: number, angleDeg: number) {
  const a = ((angleDeg - 90) * Math.PI) / 180;
  return { x: cx + r * Math.cos(a), y: cy + r * Math.sin(a) };
}

/* ────────────────────────────────────────────────────────────────── */
/* PulseDot                                                           */
/* ────────────────────────────────────────────────────────────────── */

export const PulseDot: React.FC<{ color?: string; size?: number }> = ({
  color = T.danger,
  size = 10,
}) => (
  <Box
    sx={{
      width: size, height: size, borderRadius: "50%",
      bgcolor: color, flexShrink: 0,
      animation: "eco-glow-ring 1.8s ease-out infinite",
    }}
  />
);

/* ────────────────────────────────────────────────────────────────── */
/* KpiCard — huge number + visual                                     */
/* ────────────────────────────────────────────────────────────────── */

export const KpiCard: React.FC<{
  value: number;
  label: string;
  color?: string;
  suffix?: string;
  visual?: "counter" | "gauge" | "ring" | "pulse";
  fraction?: number;
  delay?: number;
}> = ({ value, label, color = T.blueSoft, suffix = "", visual = "counter", fraction, delay = 0 }) => (
  <GlassCard delay={delay} sx={{ display: "flex", alignItems: "center", gap: 2, py: 2 }}>
    {(visual === "gauge" || visual === "ring") && (
      <RadialGauge
        fraction={fraction ?? Math.min(value / 100, 1)}
        color={color}
        size={72}
        stroke={6}
      >
        <Typography sx={{ fontWeight: 700, fontSize: "1.34rem", color: T.text }}>
          <CountUp value={value} suffix={suffix} />
        </Typography>
      </RadialGauge>
    )}
    <Box sx={{ minWidth: 0 }}>
      {visual === "counter" || visual === "pulse" ? (
        <Box sx={{ display: "flex", alignItems: "center", gap: 1.25 }}>
          {visual === "pulse" && <PulseDot color={color} />}
          <Typography
            sx={{
              fontWeight: 700,
              fontSize: "2.93rem",
              lineHeight: 1,
              color: T.text,
              letterSpacing: "-0.02em",
            }}
          >
            <CountUp value={value} suffix={suffix} />
          </Typography>
        </Box>
      ) : null}
      <Typography
        sx={{
          color: T.textDim, fontSize: "0.92rem", fontWeight: 600,
          letterSpacing: "0.12em", textTransform: "uppercase", mt: 0.75,
        }}
      >
        {label}
      </Typography>
    </Box>
  </GlassCard>
);
