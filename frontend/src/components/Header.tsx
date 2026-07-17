import React from "react";
import { AppBar, Toolbar, Typography, Box, IconButton, Tooltip } from "@mui/material";
import { RefreshCw, CircleHelp } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { T } from "@/assets/theme";

const SIDEBAR_WIDTH = 220;

const Header: React.FC = () => {
  const navigate = useNavigate();

  return (
    <AppBar
      position="fixed"
      elevation={0}
      sx={{
        zIndex: (theme) => theme.zIndex.drawer + 1,
        background: "rgba(7,11,20,0.72)",
        backdropFilter: "blur(18px)",
        borderBottom: `1px solid ${T.border}`,
        ml: `${SIDEBAR_WIDTH}px`,
        width: `calc(100% - ${SIDEBAR_WIDTH}px)`,
      }}
    >
      <Toolbar sx={{ minHeight: 48, px: 3, gap: 1.5 }}>
        <Typography
          sx={{
            fontWeight: 700, letterSpacing: "0.18em", color: T.text,
            fontSize: "0.92rem", cursor: "pointer",
          }}
          onClick={() => navigate("/")}
        >
          ECO
        </Typography>
        <Typography sx={{ color: T.textMute, fontSize: "0.92rem" }}>/</Typography>
        <Typography sx={{ color: T.textDim, fontSize: "0.92rem" }}>
          Enterprise Change Orchestrator
        </Typography>
        <Box sx={{
          px: 0.9, py: 0.2, borderRadius: "6px",
          background: `linear-gradient(90deg, ${T.blue}33, ${T.purple}26)`,
          border: `1px solid ${T.blue}44`,
        }}>
          <Typography sx={{ fontSize: "0.875rem", fontWeight: 700, color: T.blueSoft, letterSpacing: "0.06em" }}>
            v2.0
          </Typography>
        </Box>

        <Box flex={1} />

        <Tooltip title="Reload analyzer cache">
          <IconButton size="small" sx={{ color: T.textMute, "&:hover": { color: T.text } }}>
            <RefreshCw size={18} />
          </IconButton>
        </Tooltip>
        <Tooltip title="Documentation">
          <IconButton
            size="small"
            sx={{ color: T.textMute, "&:hover": { color: T.text } }}
            onClick={() => navigate("/about")}
          >
            <CircleHelp size={18} />
          </IconButton>
        </Tooltip>
      </Toolbar>
    </AppBar>
  );
};

export default Header;
