/**
 * SplashScreen — the ECO boot experience (~7s, enterprise-OS cinematic).
 *
 * Five stages, generous spacing, typography-as-logo:
 *   S1  0.0s  dark #070B15, soft particle field, faint animated grid
 *   S2  0.9s  "ECO" — huge white type (w800), subtle glow, fades in
 *   S3  1.9s  letter-spacing expands; "Enterprise Change Orchestrator"
 *             rises beneath it
 *   S4  2.8s  branding row — Powered by (watsonx) · Created using (BOB),
 *             large, white JPEG tiles removed via invert+screen compositing
 *   S5  3.9s  "Connected Enterprise" — ADLS → Databricks → Fabric Semantic
 *             Model → Power BI Reports appear sequentially with glowing
 *             outlines and drawing connectors
 *   bottom    rotating status line + hairline progress bar
 *
 * No app logic, routing, or API calls touched — visual component only.
 * The parent contract is unchanged: `onFinish` fires after exit.
 */
import React, { useEffect, useState } from "react";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import ParticleBackground from "./ParticleBackground";

const BG = "#070B15";
const IBM_BLUE = "#0F62FE";
const FABRIC_PURPLE = "#7C5CFF";
const DBX_ORANGE = "#FF6A00";
const AZURE_CYAN = "#4DD8FF";
const SANS = '"IBM Plex Sans", system-ui, sans-serif';
const EASE: [number, number, number, number] = [0.22, 1, 0.36, 1];

/* stage clocks (seconds) */
const T_ECO = 0.9;
const T_SUB = 1.9;
const T_BRAND = 2.8;
const T_CONN = 3.9;
const T_EXIT_MS = 7000;
const T_EXIT_REDUCED_MS = 1500;

const STATUS_MESSAGES = [
  "Loading enterprise metadata...",
  "Connecting Microsoft Fabric...",
  "Loading Databricks lineage...",
  "Reading Semantic Models...",
  "Building Dependency Graph...",
  "Running IBM Granite AI...",
  "Preparing Enterprise Intelligence...",
  "Launching ECO...",
] as const;

/* Connected-enterprise sequence. NOTE: IBM Branding/ has no dedicated
   Microsoft Fabric mark — the Power BI logo stands in for the Fabric
   semantic model (labelled honestly). */
const PLATFORMS = [
  { label: "Azure Data Lake Storage", logo: "/brand/adls.png", hue: AZURE_CYAN },
  { label: "Databricks Workspace", logo: "/brand/databricks.png", hue: DBX_ORANGE },
  { label: "Fabric Semantic Model", logo: "/brand/powerbi.png", hue: FABRIC_PURPLE },
  { label: "Power BI Reports", logo: "/brand/powerbi.png", hue: IBM_BLUE },
] as const;

/* Opaque-JPEG white-tile removal: invert lightness (white→black), restore
   hue, then screen-composite so black becomes transparent on the dark bg. */
const JPEG_ON_DARK: React.CSSProperties = {
  filter: "invert(1) hue-rotate(180deg)",
  mixBlendMode: "screen",
};

export interface SplashScreenProps {
  onFinish: () => void;
}

const SplashScreen: React.FC<SplashScreenProps> = ({ onFinish }) => {
  const reduced = useReducedMotion();
  const [leaving, setLeaving] = useState(false);
  const [msgIndex, setMsgIndex] = useState(0);

  /* self-scheduled exit */
  useEffect(() => {
    const t = window.setTimeout(
      () => setLeaving(true),
      reduced ? T_EXIT_REDUCED_MS : T_EXIT_MS
    );
    return () => window.clearTimeout(t);
  }, [reduced]);

  /* rotating status line */
  useEffect(() => {
    if (reduced) { setMsgIndex(STATUS_MESSAGES.length - 1); return; }
    const start = window.setTimeout(() => {
      const iv = window.setInterval(() => {
        setMsgIndex((i) => {
          if (i + 1 >= STATUS_MESSAGES.length) { window.clearInterval(iv); return i; }
          return i + 1;
        });
      }, 720);
    }, 1100);
    return () => window.clearTimeout(start);
  }, [reduced]);

  const d = (t: number) => (reduced ? 0.1 : t); // stage delay helper

  return (
    <AnimatePresence onExitComplete={onFinish}>
      {!leaving && (
        <motion.div
          key="eco-splash"
          initial={{ opacity: 1 }}
          exit={{ opacity: 0, scale: 1.015 }}
          transition={{ duration: 0.8, ease: "easeInOut" }}
          style={{
            position: "fixed",
            inset: 0,
            zIndex: 2000,
            background: BG,
            overflow: "hidden",
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            justifyContent: "center",
            fontFamily: SANS,
          }}
        >
          {/* ── S1: grid + particles ─────────────────────────────── */}
          <div
            aria-hidden
            style={{
              position: "absolute",
              inset: 0,
              backgroundImage:
                `linear-gradient(rgba(77,163,255,0.045) 1px, transparent 1px),` +
                `linear-gradient(90deg, rgba(77,163,255,0.045) 1px, transparent 1px)`,
              backgroundSize: "72px 72px",
              maskImage:
                "radial-gradient(62% 58% at 50% 44%, rgba(0,0,0,0.9), transparent)",
              WebkitMaskImage:
                "radial-gradient(62% 58% at 50% 44%, rgba(0,0,0,0.9), transparent)",
              animation: reduced ? undefined : "eco-grid-drift 34s linear infinite",
            }}
          />
          <style>{`
            @keyframes eco-grid-drift {
              from { background-position: 0 0, 0 0; }
              to   { background-position: 72px 72px, 72px 72px; }
            }
          `}</style>
          <motion.div
            aria-hidden
            style={{ position: "absolute", inset: 0, opacity: 0.55 }}
            initial={{ opacity: 0 }}
            animate={{ opacity: 0.55 }}
            transition={{ duration: 1.4 }}
          >
            <ParticleBackground opacity={0.7} density={0.45} linkDistance={130} />
          </motion.div>
          {/* vignette keeps the centre crisp */}
          <div
            aria-hidden
            style={{
              position: "absolute",
              inset: 0,
              background:
                "radial-gradient(60% 60% at 50% 44%, transparent 0%, rgba(7,11,21,0.78) 100%)",
            }}
          />

          {/* ── centre column ─────────────────────────────────────── */}
          <div
            style={{
              position: "relative",
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              paddingBottom: 96,
            }}
          >
            {/* S2+S3: ECO wordmark — the typography IS the logo */}
            <motion.h1
              initial={{ opacity: 0, filter: "blur(10px)", letterSpacing: "0.14em" }}
              animate={{
                opacity: 1,
                filter: "blur(0px)",
                letterSpacing: reduced ? "0.3em" : ["0.14em", "0.14em", "0.3em"],
              }}
              transition={{
                opacity: { delay: d(T_ECO), duration: 1.0, ease: EASE },
                filter: { delay: d(T_ECO), duration: 1.0, ease: EASE },
                letterSpacing: reduced
                  ? { duration: 0.1 }
                  : { delay: d(T_ECO), duration: 2.0, times: [0, 0.45, 1], ease: EASE },
              }}
              style={{
                margin: 0,
                fontSize: "min(104px, 15vw)",
                fontWeight: 800,
                color: "#FFFFFF",
                lineHeight: 1,
                marginRight: "-0.3em",
                textShadow: "0 0 48px rgba(15,98,254,0.28)",
              }}
            >
              ECO
            </motion.h1>

            {/* S3: subtitle rises */}
            <motion.p
              initial={{ opacity: 0, y: 22 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: d(T_SUB), duration: 0.9, ease: EASE }}
              style={{
                margin: 0,
                marginTop: 34,
                fontSize: "min(38px, 5vw)",
                fontWeight: 300,
                letterSpacing: "0.24em",
                marginRight: "-0.24em",
                color: "rgba(255,255,255,0.82)",
                textTransform: "uppercase",
                textAlign: "center",
              }}
            >
              Enterprise Change Orchestrator
            </motion.p>

            {/* S4: branding row — large, tiles removed */}
            <motion.div
              initial={{ opacity: 0, y: 26 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: d(T_BRAND), duration: 0.9, ease: EASE }}
              style={{
                marginTop: 72,
                display: "flex",
                alignItems: "flex-start",
                gap: 110,
              }}
            >
              {([
                { label: "Powered by", src: "/brand/watsonx.jpg", alt: "IBM watsonx", w: 200 },
                { label: "Created using", src: "/brand/bob.jfif", alt: "IBM BOB", w: 180 },
              ] as const).map((b) => (
                <div
                  key={b.label}
                  style={{
                    display: "flex",
                    flexDirection: "column",
                    alignItems: "center",
                    gap: 16,
                  }}
                >
                  <span
                    style={{
                      fontSize: 24,
                      fontWeight: 300,
                      letterSpacing: "0.2em",
                      marginRight: "-0.2em",
                      textTransform: "uppercase",
                      color: "rgba(255,255,255,0.5)",
                    }}
                  >
                    {b.label}
                  </span>
                  <img
                    src={b.src}
                    alt={b.alt}
                    style={{
                      width: b.w * 1.15,
                      height: "auto",
                      objectFit: "contain",
                      ...JPEG_ON_DARK,
                    }}
                  />
                </div>
              ))}
            </motion.div>

            {/* S5: connected enterprise */}
            <motion.div
              initial={{ opacity: 0, y: 24 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: d(T_CONN), duration: 0.8, ease: EASE }}
              style={{
                marginTop: 68,
                display: "flex",
                flexDirection: "column",
                alignItems: "center",
                gap: 26,
              }}
            >
              <span
                style={{
                  fontSize: 22,
                  fontWeight: 300,
                  letterSpacing: "0.28em",
                  marginRight: "-0.28em",
                  textTransform: "uppercase",
                  color: "rgba(255,255,255,0.42)",
                }}
              >
                Connected Enterprise
              </span>

              <div style={{ display: "flex", alignItems: "center", gap: 0 }}>
                {PLATFORMS.map((p, i) => (
                  <React.Fragment key={p.label}>
                    {i > 0 && (
                      /* drawing connector */
                      <motion.div
                        aria-hidden
                        initial={{ scaleX: 0 }}
                        animate={{ scaleX: 1 }}
                        transition={{ delay: d(T_CONN + 0.35 + i * 0.32), duration: 0.4, ease: EASE }}
                        style={{
                          width: 56,
                          height: 1.5,
                          transformOrigin: "left",
                          background: `linear-gradient(90deg, ${PLATFORMS[i - 1].hue}55, ${p.hue}55)`,
                        }}
                      />
                    )}
                    <motion.div
                      initial={{ opacity: 0, scale: 0.92, filter: "blur(6px)" }}
                      animate={{ opacity: 1, scale: 1, filter: "blur(0px)" }}
                      transition={{ delay: d(T_CONN + 0.2 + i * 0.32), duration: 0.6, ease: EASE }}
                      style={{
                        display: "flex",
                        flexDirection: "column",
                        alignItems: "center",
                        gap: 12,
                        padding: "20px 26px",
                        borderRadius: 16,
                        border: `1px solid ${p.hue}44`,
                        background: "rgba(255,255,255,0.02)",
                        boxShadow: `0 0 26px ${p.hue}1f, inset 0 0 18px ${p.hue}0d`,
                        minWidth: 180,
                      }}
                    >
                      <img
                        src={p.logo}
                        alt={p.label}
                        style={{ height: 52, width: "auto", objectFit: "contain" }}
                      />
                      <span
                        style={{
                          fontSize: 17,
                          fontWeight: 400,
                          letterSpacing: "0.04em",
                          color: "rgba(255,255,255,0.68)",
                          textAlign: "center",
                          maxWidth: 185,
                          lineHeight: 1.35,
                        }}
                      >
                        {p.label}
                      </span>
                    </motion.div>
                  </React.Fragment>
                ))}
              </div>
            </motion.div>
          </div>

          {/* ── bottom: rotating status + hairline progress ───────── */}
          <div
            style={{
              position: "absolute",
              bottom: 52,
              left: 0,
              right: 0,
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              gap: 20,
            }}
          >
            <div style={{ height: 30, overflow: "hidden" }} aria-live="polite">
              <AnimatePresence mode="wait">
                <motion.span
                  key={msgIndex}
                  initial={{ opacity: 0, y: 12 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, y: -12 }}
                  transition={{ duration: 0.32, ease: EASE }}
                  style={{
                    display: "block",
                    fontSize: 21,
                    fontWeight: 300,
                    letterSpacing: "0.06em",
                    color: "rgba(255,255,255,0.55)",
                  }}
                >
                  {STATUS_MESSAGES[msgIndex]}
                </motion.span>
              </AnimatePresence>
            </div>

            <div
              role="progressbar"
              aria-label="Starting ECO"
              style={{
                width: "min(520px, 62vw)",
                height: 2,
                borderRadius: 1,
                background: "rgba(255,255,255,0.08)",
                overflow: "hidden",
              }}
            >
              <motion.div
                initial={{ width: "0%" }}
                animate={{ width: "100%" }}
                transition={{
                  delay: d(T_ECO),
                  duration: reduced ? 0.8 : (T_EXIT_MS / 1000) - T_ECO - 0.2,
                  ease: "easeInOut",
                }}
                style={{
                  height: "100%",
                  borderRadius: 1,
                  background: `linear-gradient(90deg, ${IBM_BLUE}, ${AZURE_CYAN})`,
                  boxShadow: `0 0 10px ${IBM_BLUE}88`,
                }}
              />
            </div>
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  );
};

export default SplashScreen;
