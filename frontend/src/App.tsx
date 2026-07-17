import React, { Suspense, lazy } from "react";
import { BrowserRouter, Routes, Route } from "react-router-dom";
import { Box, LinearProgress } from "@mui/material";
import { motion } from "framer-motion";
import AppLayout from "@/layouts/AppLayout";
import SplashScreen from "@/components/SplashScreen";
import useSplash from "@/hooks/useSplash";

const Home            = lazy(() => import("@/pages/Home"));
const Analysis        = lazy(() => import("@/pages/Analysis"));
const DependencyGraph = lazy(() => import("@/pages/DependencyGraph"));
const About           = lazy(() => import("@/pages/About"));

const PageLoader: React.FC = () => (
  <Box sx={{ pt: 0 }}>
    <LinearProgress />
  </Box>
);

const App: React.FC = () => {
  const { showSplash, completeSplash } = useSplash();
  const [entered, setEntered] = React.useState(!showSplash);

  return (
    <BrowserRouter>
      {showSplash && (
        <SplashScreen
          onFinish={() => {
            completeSplash();
            setEntered(true);
          }}
        />
      )}
      {/* Home fades upward as the splash releases — no hard cut. */}
      <motion.div
        initial={showSplash ? { opacity: 0, y: 24 } : false}
        animate={entered ? { opacity: 1, y: 0 } : { opacity: 0, y: 24 }}
        transition={{ duration: 0.7, ease: [0.16, 1, 0.3, 1] }}
      >
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
      </motion.div>
    </BrowserRouter>
  );
};

export default App;
