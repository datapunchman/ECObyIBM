import React from "react";
import { Paper, Box, Typography, Chip, Divider } from "@mui/material";
import StorageIcon from "@mui/icons-material/Storage";
import TableChartOutlinedIcon from "@mui/icons-material/TableChartOutlined";
import CodeOutlinedIcon from "@mui/icons-material/CodeOutlined";
import AccountTreeOutlinedIcon from "@mui/icons-material/AccountTreeOutlined";
import BarChartOutlinedIcon from "@mui/icons-material/BarChartOutlined";
import ApiOutlinedIcon from "@mui/icons-material/ApiOutlined";

const SYSTEM_CONFIG: Record<
  string,
  { label: string; color: string; bgColor: string; Icon: React.ElementType }
> = {
  database:   { label: "Database",   color: "#6929c4", bgColor: "#f6f2ff", Icon: StorageIcon },
  sql:        { label: "SQL",        color: "#005d5d", bgColor: "#d9fbfb", Icon: TableChartOutlinedIcon },
  databricks: { label: "Databricks", color: "#ff6200", bgColor: "#fff2e8", Icon: CodeOutlinedIcon },
  pipeline:   { label: "Pipeline",   color: "#0043ce", bgColor: "#edf5ff", Icon: AccountTreeOutlinedIcon },
  powerbi:    { label: "Power BI",   color: "#be95ff", bgColor: "#f6f2ff", Icon: BarChartOutlinedIcon },
  api:        { label: "API",        color: "#007d79", bgColor: "#d9fbfb", Icon: ApiOutlinedIcon },
};

interface SystemImpactCardProps {
  system: string;
  items: string[];
}

const SystemImpactCard: React.FC<SystemImpactCardProps> = ({ system, items }) => {
  if (!items.length) return null;
  const cfg = SYSTEM_CONFIG[system] ?? {
    label: system.toUpperCase(),
    color: "#697077",
    bgColor: "#f2f4f8",
    Icon: StorageIcon,
  };
  const { Icon } = cfg;

  return (
    <Paper
      variant="outlined"
      sx={{ p: 2.5, borderColor: "divider", height: "100%" }}
    >
      <Box display="flex" alignItems="center" gap={1} mb={1.5}>
        <Box
          sx={{
            width: 32,
            height: 32,
            borderRadius: 1,
            bgcolor: cfg.bgColor,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
          }}
        >
          <Icon sx={{ fontSize: 18, color: cfg.color }} />
        </Box>
        <Box>
          <Typography variant="overline" sx={{ color: cfg.color, lineHeight: 1 }}>
            {cfg.label}
          </Typography>
          <Typography variant="caption" display="block" color="text.secondary">
            {items.length} affected
          </Typography>
        </Box>
      </Box>

      <Divider sx={{ mb: 1.5 }} />

      <Box display="flex" flexWrap="wrap" gap={0.75}>
        {items.map((item) => (
          <Chip
            key={item}
            label={item}
            size="small"
            variant="outlined"
            sx={{
              fontFamily: '"IBM Plex Mono", monospace',
              fontSize: "0.72rem",
              borderColor: cfg.color,
              color: cfg.color,
              borderRadius: 1,
            }}
          />
        ))}
      </Box>
    </Paper>
  );
};

export default SystemImpactCard;
