import React from "react";
import { Paper, Box, Typography } from "@mui/material";
import FiberManualRecordIcon from "@mui/icons-material/FiberManualRecord";

interface SummaryCardProps {
  title: string;
  /** May be undefined/null when the backend omits the field — renders empty state. */
  content?: string | null;
}

const SummaryCard: React.FC<SummaryCardProps> = ({ title, content }) => {
  const text = content ?? "";
  const lines = text
    .split("\n")
    .map((l) => l.trim())
    .filter(Boolean);

  return (
    <Paper variant="outlined" sx={{ p: 3, borderColor: "divider" }}>
      <Typography
        variant="overline"
        sx={{ color: "text.secondary", mb: 1.5, display: "block" }}
      >
        {title}
      </Typography>
      {lines.length === 0 ? (
        <Typography variant="body2" color="text.disabled">
          No information available.
        </Typography>
      ) : (
        <Box>
          {lines.map((line, i) => (
            <Box key={i} display="flex" alignItems="flex-start" gap={1} mb={0.75}>
              <FiberManualRecordIcon
                sx={{ fontSize: 7, color: "#0f62fe", mt: "7px", flexShrink: 0 }}
              />
              <Typography
                variant="body2"
                sx={{ color: "text.primary", lineHeight: 1.65 }}
              >
                {line}
              </Typography>
            </Box>
          ))}
        </Box>
      )}
    </Paper>
  );
};

export default SummaryCard;
