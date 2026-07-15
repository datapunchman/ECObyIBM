import React, { Suspense, lazy } from "react";
import { BrowserRouter, Routes, Route } from "react-router-dom";
import { Box, LinearProgress } from "@mui/material";
import AppLayout from "@/layouts/AppLayout";

const Home            = lazy(() => import("@/pages/Home"));
const Analysis        = lazy(() => import("@/pages/Analysis"));
const DependencyGraph = lazy(() => import("@/pages/DependencyGraph"));
const About           = lazy(() => import("@/pages/About"));

const PageLoader: React.FC = () => (
  <Box sx={{ pt: 0 }}>
    <LinearProgress />
  </Box>
);

const App: React.FC = () => (
  <BrowserRouter>
    <AppLayout>
      <Suspense fallback={<PageLoader />}>
        <Routes>
          <Route path="/"         element={<Home />} />
          <Route path="/analysis" element={<Analysis />} />
          <Route path="/graph"    element={<DependencyGraph />} />
          <Route path="/about"    element={<About />} />
        </Routes>
      </Suspense>
    </AppLayout>
  </BrowserRouter>
);

export default App;
