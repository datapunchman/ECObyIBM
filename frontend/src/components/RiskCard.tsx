import React from "react";
import { Paper, Box, Typography, Chip } from "@mui/material";
import { useTheme } from "@mui/material/styles";
import type { RiskLevel } from "@/types";

const RISK_CONFIG: Record<
  RiskLevel,
  { label: string; bg: string; color: string; border: string }
> = {
  low:      { label: "LOW RISK",      bg: "#defbe6", color: "#0e6027", border: "#42be65" },
  medium:   { label: "MEDIUM RISK",   bg: "#fcf4d6", color: "#684e00", border: "#f1c21b" },
  high:     { label: "HIGH RISK",     bg: "#fff1e8", color: "#b93b00", border: "#ff832b" },
  critical: { label: "CRITICAL RISK", bg: "#fff1f1", color: "#a2191f", border: "#da1e28" },
};

interface RiskCardProps {
  riskLevel: RiskLevel;
  rationale: string;
  dependenciesImpacted: number;
}

const RiskCard: React.FC<RiskCardProps> = ({
  riskLevel,
  rationale,
  dependenciesImpacted,
}) => {
  const theme = useTheme();
  void theme;
  const cfg = RISK_CONFIG[riskLevel] ?? RISK_CONFIG.medium;

  return (
    <Paper
      variant="outlined"
      sx={{
        p: 3,
        borderLeft: `4px solid ${cfg.border}`,
        bgcolor: cfg.bg,
        borderColor: cfg.border,
      }}
    >
      <Box display="flex" alignItems="center" gap={1.5} mb={1.5}>
        <Chip
          label={cfg.label}
          size="small"
          sx={{
            bgcolor: cfg.border,
            color: "#fff",
            fontWeight: 700,
            fontSize: "0.7rem",
            letterSpacing: "0.06em",
            borderRadius: 1,
          }}
        />
        <Typography variant="caption" sx={{ color: cfg.color, fontWeight: 500 }}>
          {dependenciesImpacted} dependency edge
          {dependenciesImpacted !== 1 ? "s" : ""} impacted
        </Typography>
      </Box>
      <Typography
        variant="body2"
        sx={{ color: cfg.color, fontStyle: "italic", lineHeight: 1.6 }}
      >
        {rationale}
      </Typography>
    </Paper>
  );
};

export default RiskCard;
