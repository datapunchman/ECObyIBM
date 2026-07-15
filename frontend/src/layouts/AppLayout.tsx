import React from "react";
import { Box, CssBaseline } from "@mui/material";
import Header from "@/components/Header";
import Sidebar from "@/components/Sidebar";

const SIDEBAR_WIDTH = 220;
const HEADER_HEIGHT = 48;

interface AppLayoutProps {
  children: React.ReactNode;
}

const AppLayout: React.FC<AppLayoutProps> = ({ children }) => (
  <Box sx={{ display: "flex", minHeight: "100vh", bgcolor: "background.default" }}>
    <CssBaseline />
    <Sidebar />
    <Box
      component="main"
      sx={{
        flexGrow: 1,
        ml: `${SIDEBAR_WIDTH}px`,
        mt: `${HEADER_HEIGHT}px`,
        minHeight: `calc(100vh - ${HEADER_HEIGHT}px)`,
        overflow: "auto",
      }}
    >
      <Header />
      {children}
    </Box>
  </Box>
);

export default AppLayout;
