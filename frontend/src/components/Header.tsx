import React from "react";
import {
  AppBar,
  Toolbar,
  Typography,
  Box,
  Chip,
  IconButton,
  Tooltip,
} from "@mui/material";
import RefreshIcon from "@mui/icons-material/Refresh";
import HelpOutlineIcon from "@mui/icons-material/HelpOutline";
import { useNavigate } from "react-router-dom";

const SIDEBAR_WIDTH = 220;

const Header: React.FC = () => {
  const navigate = useNavigate();

  return (
    <AppBar
      position="fixed"
      elevation={0}
      sx={{
        zIndex: (theme) => theme.zIndex.drawer + 1,
        bgcolor: "#121619",
        borderBottom: "1px solid #393939",
        ml: `${SIDEBAR_WIDTH}px`,
        width: `calc(100% - ${SIDEBAR_WIDTH}px)`,
      }}
    >
      <Toolbar sx={{ minHeight: 48, px: 3 }}>
        <Typography
          variant="body2"
          sx={{
            fontWeight: 600,
            letterSpacing: "0.04em",
            color: "#f2f4f8",
            textTransform: "uppercase",
            fontSize: "0.75rem",
            cursor: "pointer",
          }}
          onClick={() => navigate("/")}
        >
          ECO
        </Typography>

        <Typography
          variant="body2"
          sx={{ color: "#697077", mx: 1.5, fontSize: "0.75rem" }}
        >
          /
        </Typography>

        <Typography
          variant="body2"
          sx={{ color: "#c6c6c6", fontSize: "0.75rem" }}
        >
          Enterprise Change Orchestrator
        </Typography>

        <Box sx={{ ml: 2 }}>
          <Chip
            label="v1.0"
            size="small"
            sx={{
              height: 18,
              fontSize: "0.65rem",
              fontWeight: 600,
              bgcolor: "#0043ce",
              color: "#fff",
              borderRadius: 1,
            }}
          />
        </Box>

        <Box flex={1} />

        <Tooltip title="Reload analyzer cache">
          <IconButton size="small" sx={{ color: "#697077", mr: 0.5 }}>
            <RefreshIcon fontSize="small" />
          </IconButton>
        </Tooltip>

        <Tooltip title="Documentation">
          <IconButton
            size="small"
            sx={{ color: "#697077" }}
            onClick={() => navigate("/about")}
          >
            <HelpOutlineIcon fontSize="small" />
          </IconButton>
        </Tooltip>
      </Toolbar>
    </AppBar>
  );
};

export default Header;
