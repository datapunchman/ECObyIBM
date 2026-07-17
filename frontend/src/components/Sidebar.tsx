import React from "react";
import {
  Drawer, List, ListItemButton, ListItemIcon, ListItemText, Box, Typography,
} from "@mui/material";
import { motion } from "framer-motion";
import { Home, ScanSearch, Network, Info } from "lucide-react";
import { useLocation, useNavigate } from "react-router-dom";
import { T } from "@/assets/theme";
import { LogoMark } from "./Logo";

const WIDTH = 220;

const navItems = [
  { label: "Home", path: "/", Icon: Home },
  { label: "Analysis", path: "/analysis", Icon: ScanSearch },
  { label: "Dependency Graph", path: "/graph", Icon: Network },
  { label: "About", path: "/about", Icon: Info },
];

const Sidebar: React.FC = () => {
  const location = useLocation();
  const navigate = useNavigate();

  return (
    <Drawer
      variant="permanent"
      sx={{
        width: WIDTH,
        flexShrink: 0,
        "& .MuiDrawer-paper": {
          width: WIDTH,
          boxSizing: "border-box",
          background: "linear-gradient(180deg, rgba(13,18,32,0.92) 0%, rgba(7,11,20,0.96) 100%)",
          backdropFilter: "blur(20px)",
          borderRight: `1px solid ${T.border}`,
          display: "flex",
          flexDirection: "column",
        },
      }}
    >
      {/* Logo lockup */}
      <Box
        sx={{ px: 2.5, py: 2.5, display: "flex", alignItems: "center", gap: 1.5, cursor: "pointer" }}
        onClick={() => navigate("/")}
      >
        <LogoMark size={38} variant="blue" />
        <Box>
          <Typography sx={{
            color: T.text, fontWeight: 700, fontSize: "1.22rem",
            letterSpacing: "0.22em", lineHeight: 1,
          }}>
            ECO
          </Typography>
          <Typography sx={{
            color: T.textMute, fontSize: "0.875rem", letterSpacing: "0.08em",
            textTransform: "uppercase", mt: 0.4, lineHeight: 1.3, display: "block",
          }}>
            Enterprise Change
            <br />
            Orchestrator
          </Typography>
        </Box>
      </Box>

      {/* Navigation */}
      <List disablePadding sx={{ mt: 1, flex: 1, px: 1 }}>
        {navItems.map(({ label, path, Icon }) => {
          const active = location.pathname === path;
          return (
            <Box key={path} sx={{ position: "relative", mb: 0.5 }}>
              {active && (
                <motion.div
                  layoutId="eco-nav-pill"
                  transition={{ type: "spring", stiffness: 420, damping: 34 }}
                  style={{
                    position: "absolute", inset: 0, borderRadius: 10,
                    background: `linear-gradient(90deg, ${T.blue}2e, ${T.blue}14)`,
                    border: `1px solid ${T.blue}55`,
                    boxShadow: `0 0 18px ${T.blue}22`,
                  }}
                />
              )}
              <ListItemButton
                onClick={() => navigate(path)}
                sx={{
                  borderRadius: "10px", py: 0.9, position: "relative",
                  "&:hover": { bgcolor: active ? "transparent" : "rgba(148,163,184,0.06)" },
                }}
              >
                <ListItemIcon sx={{ minWidth: 34 }}>
                  <Icon size={20} color={active ? T.blueSoft : (T.textMute as string)} />
                </ListItemIcon>
                <ListItemText
                  primary={label}
                  primaryTypographyProps={{
                    fontSize: "1.02rem",
                    fontWeight: active ? 600 : 400,
                    color: active ? T.text : T.textDim,
                  }}
                />
              </ListItemButton>
            </Box>
          );
        })}
      </List>

      {/* Footer */}
      <Box sx={{ px: 2.5, py: 2, borderTop: `1px solid ${T.border}` }}>
        <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
          <Box sx={{
            width: 6, height: 6, borderRadius: "50%", bgcolor: T.success,
            boxShadow: `0 0 8px ${T.success}`,
            animation: "eco-pulse 2.6s ease-in-out infinite",
          }} />
          <Typography sx={{ color: T.textMute, fontSize: "0.875rem" }}>
            Powered by IBM Granite
          </Typography>
        </Box>
      </Box>
    </Drawer>
  );
};

export default Sidebar;
