import React from "react";
import {
  Drawer,
  List,
  ListItemButton,
  ListItemIcon,
  ListItemText,
  Box,
  Typography,
  Divider,
} from "@mui/material";
import HomeOutlinedIcon from "@mui/icons-material/HomeOutlined";
import SearchOutlinedIcon from "@mui/icons-material/SearchOutlined";
import AccountTreeOutlinedIcon from "@mui/icons-material/AccountTreeOutlined";
import InfoOutlinedIcon from "@mui/icons-material/InfoOutlined";
import { useLocation, useNavigate } from "react-router-dom";

const WIDTH = 220;

const navItems = [
  { label: "Home", path: "/", Icon: HomeOutlinedIcon },
  { label: "Analysis", path: "/analysis", Icon: SearchOutlinedIcon },
  { label: "Dependency Graph", path: "/graph", Icon: AccountTreeOutlinedIcon },
  { label: "About", path: "/about", Icon: InfoOutlinedIcon },
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
          bgcolor: "#161616",
          borderRight: "1px solid #393939",
          display: "flex",
          flexDirection: "column",
        },
      }}
    >
      {/* Logo lockup */}
      <Box sx={{ px: 3, py: 2.5 }}>
        <Typography
          variant="h6"
          sx={{
            color: "#f2f4f8",
            fontWeight: 700,
            fontSize: "1rem",
            letterSpacing: "0.01em",
          }}
        >
          ECO
        </Typography>
        <Typography
          variant="caption"
          sx={{ color: "#697077", display: "block", mt: 0.25, lineHeight: 1.4 }}
        >
          Enterprise Change
          <br />
          Orchestrator
        </Typography>
      </Box>

      <Divider sx={{ borderColor: "#393939" }} />

      {/* Navigation */}
      <List disablePadding sx={{ mt: 1, flex: 1 }}>
        {navItems.map(({ label, path, Icon }) => {
          const active = location.pathname === path;
          return (
            <ListItemButton
              key={path}
              selected={active}
              onClick={() => navigate(path)}
              sx={{
                mx: 1,
                mb: 0.25,
                borderRadius: 1,
                py: 0.875,
                "&.Mui-selected": {
                  bgcolor: "#0043ce",
                  "&:hover": { bgcolor: "#0043ce" },
                },
                "&:hover": { bgcolor: "#262626" },
              }}
            >
              <ListItemIcon sx={{ minWidth: 36, color: active ? "#fff" : "#8d8d8d" }}>
                <Icon sx={{ fontSize: 18 }} />
              </ListItemIcon>
              <ListItemText
                primary={label}
                primaryTypographyProps={{
                  fontSize: "0.8125rem",
                  fontWeight: active ? 600 : 400,
                  color: active ? "#fff" : "#c6c6c6",
                }}
              />
            </ListItemButton>
          );
        })}
      </List>

      {/* Footer */}
      <Box sx={{ px: 3, py: 2, borderTop: "1px solid #393939" }}>
        <Typography variant="caption" sx={{ color: "#525252", fontSize: "0.7rem" }}>
          Powered by IBM Granite
        </Typography>
      </Box>
    </Drawer>
  );
};

export default Sidebar;
