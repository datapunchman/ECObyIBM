/**
 * Logo — the ECO mark and wordmark.
 *
 * Geometry (v2 — "Impact Lattice")
 * --------------------------------
 * Six nodes on a hexagonal lattice joined by directed struts. The three
 * left nodes with their horizontal struts read as an abstract "E"; the
 * whole figure reads as a dependency graph radiating from a source —
 * enterprise, graph, impact. No rings, no leaves, no eco symbolism.
 *
 *      ●───────●
 *      │        ╲
 *      ●─────────◉   ← impact node (accent)
 *      │        ╱
 *      ●───────●
 *
 * Solid colour only (no gradients), rounded caps, monochrome-safe.
 */
import React from "react";

/* ---------------------------------------------------------------- */
/* Shared geometry — consumed by GraphAnimation for the morph        */
/* ---------------------------------------------------------------- */

export const ECO_VIEWBOX = 64;

/* Lattice anchors (viewBox units) */
const L = 17;         // left column x
const MX = 41;        // mid column x
const RX = 52;        // impact node x
const TY = 16;        // top row y
const CY = 32;        // centre row y
const BY = 48;        // bottom row y

/** Spine: the left column drawn as one path (kept as ECO_ARC_PATH for
    backward compatibility with GraphAnimation's draw choreography). */
export const ECO_ARC_PATH = `M ${L} ${TY} L ${L} ${BY}`;

/** Struts — [x1, y1, x2, y2]: three E-arms + two convergence diagonals. */
export const ECO_BARS: ReadonlyArray<readonly [number, number, number, number]> = [
  [L, TY, MX, TY],       // top arm
  [L, CY, RX, CY],       // centre arm — pierces to the impact node
  [L, BY, MX, BY],       // bottom arm
  [MX, TY, RX, CY],      // top convergence
  [MX, BY, RX, CY],      // bottom convergence
];

/** Graph nodes: lattice anchors. Last one is the impact node. */
export const ECO_NODES: ReadonlyArray<readonly [number, number]> = [
  [L, TY],
  [L, CY],
  [L, BY],
  [MX, TY],
  [MX, BY],
  [RX, CY],
];

export const ECO_STROKE = 4.6;
export const ECO_NODE_R = 3.1;
/** Index of the accent "impact" node in ECO_NODES. */
export const ECO_IMPACT_NODE = 5;

/* ---------------------------------------------------------------- */
/* Colours                                                           */
/* ---------------------------------------------------------------- */

export const ECO_BLUE = "#0F62FE";
export const ECO_ACCENT = "#4DA3FF";

type LogoVariant = "blue" | "white" | "mono";

export interface LogoMarkProps {
  size?: number;
  variant?: LogoVariant;
  /** Accent colour for the impact node; defaults per variant. */
  nodeColor?: string;
  title?: string;
  style?: React.CSSProperties;
}

function markColor(variant: LogoVariant): string {
  if (variant === "white") return "#FFFFFF";
  if (variant === "mono") return "currentColor";
  return ECO_BLUE;
}

/** The bare ECO mark (no text). */
export const LogoMark: React.FC<LogoMarkProps> = ({
  size = 48,
  variant = "blue",
  nodeColor,
  title = "ECO",
  style,
}) => {
  const stroke = markColor(variant);
  const accent = nodeColor ?? (variant === "blue" ? ECO_ACCENT : stroke);

  return (
    <svg
      width={size}
      height={size}
      viewBox={`0 0 ${ECO_VIEWBOX} ${ECO_VIEWBOX}`}
      fill="none"
      role="img"
      aria-label={title}
      style={style}
    >
      <path
        d={ECO_ARC_PATH}
        stroke={stroke}
        strokeWidth={ECO_STROKE}
        strokeLinecap="round"
      />
      {ECO_BARS.map(([x1, y1, x2, y2], i) => (
        <line
          key={i}
          x1={x1}
          y1={y1}
          x2={x2}
          y2={y2}
          stroke={stroke}
          strokeWidth={ECO_STROKE}
          strokeLinecap="round"
        />
      ))}
      {ECO_NODES.map(([cx, cy], i) => (
        <circle
          key={i}
          cx={cx}
          cy={cy}
          r={i === ECO_IMPACT_NODE ? ECO_NODE_R + 1 : ECO_NODE_R}
          fill={i === ECO_IMPACT_NODE ? accent : stroke}
        />
      ))}
    </svg>
  );
};

/* ---------------------------------------------------------------- */
/* Horizontal lockup: mark + wordmark                                */
/* ---------------------------------------------------------------- */

export interface LogoProps extends LogoMarkProps {
  /** Wordmark + subline text colour. Defaults per variant. */
  textColor?: string;
  subline?: boolean;
}

const Logo: React.FC<LogoProps> = ({
  size = 40,
  variant = "blue",
  textColor,
  subline = true,
  ...rest
}) => {
  const text =
    textColor ?? (variant === "white" || variant === "mono" ? "currentColor" : "#121619");

  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: size * 0.35,
        color: text,
      }}
    >
      <LogoMark size={size} variant={variant} {...rest} />
      <span style={{ display: "flex", flexDirection: "column", lineHeight: 1 }}>
        <span
          style={{
            fontFamily: '"IBM Plex Sans", system-ui, sans-serif',
            fontWeight: 600,
            fontSize: size * 0.52,
            letterSpacing: "0.30em",
            marginRight: "-0.30em",
          }}
        >
          ECO
        </span>
        {subline && (
          <span
            style={{
              fontFamily: '"IBM Plex Sans", system-ui, sans-serif',
              fontWeight: 400,
              fontSize: size * 0.19,
              letterSpacing: "0.16em",
              marginRight: "-0.16em",
              opacity: 0.62,
              marginTop: size * 0.12,
              textTransform: "uppercase",
              whiteSpace: "nowrap",
            }}
          >
            Enterprise Change Orchestrator
          </span>
        )}
      </span>
    </span>
  );
};

export default Logo;
