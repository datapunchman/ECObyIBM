import React from "react";
import { Paper, Box, Typography } from "@mui/material";
import FiberManualRecordIcon from "@mui/icons-material/FiberManualRecord";

interface SummaryCardProps {
  title: string;
  content: string;
}

const SummaryCard: React.FC<SummaryCardProps> = ({ title, content }) => (
  <Paper variant="outlined" sx={{ p: 3, borderColor: "divider" }}>
    <Typography
      variant="overline"
      sx={{ color: "text.secondary", mb: 1.5, display: "block" }}
    >
      {title}
    </Typography>
    <Box>
      {content.split("\n").map((line, i) => {
        const trimmed = line.trim();
        if (!trimmed) return null;
        return (
          <Box key={i} display="flex" alignItems="flex-start" gap={1} mb={0.75}>
            <FiberManualRecordIcon
              sx={{ fontSize: 7, color: "#0f62fe", mt: "7px", flexShrink: 0 }}
            />
            <Typography
              variant="body2"
              sx={{ color: "text.primary", lineHeight: 1.65 }}
            >
              {trimmed}
            </Typography>
          </Box>
        );
      })}
    </Box>
  </Paper>
);

export default SummaryCard;
