import { createTheme } from "@mui/material/styles";

// ─────────────────────────────────────────────────────────────────────────────
// ECO design tokens — dark-only "Enterprise AI OS"
// ─────────────────────────────────────────────────────────────────────────────

export const T = {
  bg:      "#070B14",
  card:    "#111827",
  cardUp:  "#161F2E",
  border:  "rgba(148, 163, 184, 0.10)",
  borderUp:"rgba(148, 163, 184, 0.18)",
  blue:    "#0F62FE",
  blueSoft:"#4DA3FF",
  purple:  "#8B5CF6",
  cyan:    "#22D3EE",
  danger:  "#EF4444",
  success: "#10B981",
  amber:   "#F59E0B",
  text:    "#F8FAFC",
  textDim: "rgba(248, 250, 252, 0.60)",
  textMute:"rgba(248, 250, 252, 0.38)",
  mono:    '"IBM Plex Mono", ui-monospace, monospace',
  sans:    '"IBM Plex Sans", "Segoe UI", system-ui, sans-serif',
  /** glass surface */
  glass: {
    background: "linear-gradient(160deg, rgba(17,24,39,0.86) 0%, rgba(17,24,39,0.62) 100%)",
    backdropFilter: "blur(18px) saturate(140%)",
    border: "1px solid rgba(148,163,184,0.10)",
    boxShadow: "0 8px 32px rgba(2,6,17,0.55), inset 0 1px 0 rgba(255,255,255,0.04)",
  },
  ease: [0.16, 1, 0.3, 1] as [number, number, number, number],
} as const;

export const SYSTEM_HUES: Record<string, string> = {
  database:   T.purple,
  sql:        T.cyan,
  databricks: "#F97316",
  pipeline:   T.blue,
  powerbi:    "#E879F9",
  api:        T.success,
};

export const RISK_HUES: Record<string, string> = {
  low:      T.success,
  medium:   T.amber,
  high:     "#F97316",
  critical: T.danger,
};

export const theme = createTheme({
  palette: {
    mode: "dark",
    primary:   { main: T.blue, light: T.blueSoft, dark: "#0043CE", contrastText: "#fff" },
    secondary: { main: T.purple },
    info:      { main: T.cyan },
    error:     { main: T.danger },
    success:   { main: T.success },
    warning:   { main: T.amber },
    background: { default: T.bg, paper: T.card },
    text: { primary: T.text, secondary: T.textDim, disabled: T.textMute },
    divider: T.border,
  },
  typography: {
    fontFamily: T.sans,
    h1: { fontWeight: 700, letterSpacing: "-0.03em" },
    h2: { fontWeight: 700, letterSpacing: "-0.02em" },
    h3: { fontWeight: 600, letterSpacing: "-0.02em" },
    h4: { fontWeight: 600, letterSpacing: "-0.01em" },
    h5: { fontWeight: 600 },
    h6: { fontWeight: 600 },
    body1: { fontSize: "1.2rem", lineHeight: 1.6 },
    body2: { fontSize: "1.09rem", lineHeight: 1.55 },
    caption: { color: T.textDim },
    overline: { letterSpacing: "0.14em", fontWeight: 600, color: T.textDim, fontSize: "0.875rem" },
  },
  shape: { borderRadius: 12 },
  components: {
    MuiCssBaseline: {
      styleOverrides: {
        body: { backgroundColor: T.bg },
      },
    },
    MuiButton: {
      styleOverrides: {
        root: {
          textTransform: "none",
          fontWeight: 600,
          fontSize: "1.05rem",
          borderRadius: 10,
          transition: "transform 180ms cubic-bezier(0.16,1,0.3,1), box-shadow 180ms",
          "&:hover": { transform: "translateY(-1px)" },
          "&:active": { transform: "translateY(0)" },
        },
        containedPrimary: {
          boxShadow: "0 4px 20px rgba(15,98,254,0.35)",
          "&:hover": { boxShadow: "0 6px 28px rgba(15,98,254,0.5)", backgroundColor: "#2D7BFE" },
        },
        outlined: { borderColor: T.borderUp },
      },
    },
    MuiPaper: {
      styleOverrides: {
        root: {
          backgroundImage: "none",
          backgroundColor: T.card,
          border: `1px solid ${T.border}`,
        },
        outlined: { borderColor: T.border },
      },
    },
    MuiChip: {
      styleOverrides: {
        root: { borderRadius: 8, fontWeight: 500 },
      },
    },
    MuiTooltip: {
      styleOverrides: {
        tooltip: {
          backgroundColor: "rgba(17,24,39,0.95)",
          border: `1px solid ${T.borderUp}`,
          backdropFilter: "blur(12px)",
          fontSize: "0.95rem",
        },
      },
    },
    MuiLinearProgress: {
      styleOverrides: { root: { borderRadius: 4, height: 3, backgroundColor: "rgba(148,163,184,0.12)" } },
    },
    MuiAccordion: {
      styleOverrides: {
        root: {
          backgroundColor: "transparent",
          backgroundImage: "none",
          "&:before": { display: "none" },
        },
      },
    },
  },
  custom: {
    risk: RISK_HUES,
  },
});

// Augment the MUI theme to include custom keys
declare module "@mui/material/styles" {
  interface Theme {
    custom: {
      risk: Record<string, string>;
    };
  }
  interface ThemeOptions {
    custom?: {
      risk?: Record<string, string>;
    };
  }
}
