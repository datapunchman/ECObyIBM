import React from "react";
import { Backdrop, Box, CircularProgress, Typography } from "@mui/material";

interface LoadingOverlayProps {
  open: boolean;
  message?: string;
}

const LoadingOverlay: React.FC<LoadingOverlayProps> = ({
  open,
  message = "Running impact analysis…",
}) => (
  <Backdrop
    open={open}
    sx={{
      zIndex: (theme) => theme.zIndex.modal + 1,
      bgcolor: "rgba(18,22,25,0.88)",
      flexDirection: "column",
      gap: 2,
    }}
  >
    <CircularProgress size={48} thickness={3} sx={{ color: "#0f62fe" }} />
    <Box textAlign="center">
      <Typography variant="body1" sx={{ color: "#f2f4f8", fontWeight: 500 }}>
        {message}
      </Typography>
      <Typography variant="caption" sx={{ color: "#697077" }}>
        IBM Granite is analysing downstream impact
      </Typography>
    </Box>
  </Backdrop>
);

export default LoadingOverlay;
