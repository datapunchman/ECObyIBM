import { createTheme } from "@mui/material/styles";

// IBM design system palette
const IBM_BLUE = "#0f62fe";
const IBM_BLUE_DARK = "#0043ce";
const IBM_BLUE_LIGHT = "#d0e2ff";
const COOL_GRAY_10 = "#f2f4f8";
const COOL_GRAY_20 = "#dde1e6";
const COOL_GRAY_60 = "#697077";
const COOL_GRAY_100 = "#121619";
const WHITE = "#ffffff";
const RED_60 = "#da1e28";
const ORANGE_40 = "#ff832b";
const GREEN_40 = "#42be65";
const YELLOW_20 = "#f1c21b";

export const theme = createTheme({
  palette: {
    mode: "light",
    primary: {
      main: IBM_BLUE,
      dark: IBM_BLUE_DARK,
      light: IBM_BLUE_LIGHT,
      contrastText: WHITE,
    },
    secondary: {
      main: COOL_GRAY_60,
    },
    background: {
      default: COOL_GRAY_10,
      paper: WHITE,
    },
    text: {
      primary: COOL_GRAY_100,
      secondary: COOL_GRAY_60,
    },
    error: { main: RED_60 },
    warning: { main: ORANGE_40 },
    success: { main: GREEN_40 },
    divider: COOL_GRAY_20,
  },
  typography: {
    fontFamily: '"IBM Plex Sans", "Segoe UI", system-ui, sans-serif',
    h1: { fontWeight: 600, letterSpacing: "-0.02em" },
    h2: { fontWeight: 600, letterSpacing: "-0.01em" },
    h3: { fontWeight: 600 },
    h4: { fontWeight: 500 },
    h5: { fontWeight: 500 },
    h6: { fontWeight: 500 },
    body1: { fontSize: "0.9375rem", lineHeight: 1.6 },
    body2: { fontSize: "0.875rem", lineHeight: 1.5 },
    caption: { color: COOL_GRAY_60 },
    overline: { letterSpacing: "0.1em", fontWeight: 500, color: COOL_GRAY_60 },
  },
  shape: { borderRadius: 2 },
  shadows: [
    "none",
    "0 1px 2px rgba(0,0,0,0.07)",
    "0 1px 4px rgba(0,0,0,0.10)",
    "0 2px 8px rgba(0,0,0,0.10)",
    "0 4px 12px rgba(0,0,0,0.10)",
    "0 8px 24px rgba(0,0,0,0.10)",
    ...Array(19).fill("none"),
  ] as ReturnType<typeof createTheme>["shadows"],
  components: {
    MuiButton: {
      styleOverrides: {
        root: {
          textTransform: "none",
          fontWeight: 500,
          borderRadius: 2,
        },
        containedPrimary: {
          "&:hover": { backgroundColor: IBM_BLUE_DARK },
        },
      },
    },
    MuiPaper: {
      styleOverrides: {
        root: { backgroundImage: "none" },
      },
    },
    MuiChip: {
      styleOverrides: {
        root: { borderRadius: 2, fontWeight: 500 },
      },
    },
    MuiTableCell: {
      styleOverrides: {
        head: { fontWeight: 600, backgroundColor: COOL_GRAY_10 },
      },
    },
    MuiAlert: {
      styleOverrides: {
        root: { borderRadius: 2 },
      },
    },
    MuiLinearProgress: {
      styleOverrides: {
        root: { borderRadius: 2, height: 3 },
      },
    },
  },
  // expose custom risk colours so components can use them without hardcoding
  custom: {
    risk: {
      low: GREEN_40,
      medium: YELLOW_20,
      high: ORANGE_40,
      critical: RED_60,
    },
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
