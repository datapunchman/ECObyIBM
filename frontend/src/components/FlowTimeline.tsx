/**
 * FlowTimeline — animated vertical timeline (deployment / rollback).
 *
 * Steps connect with a line that draws downward; each node pops in with a
 * spring; hovering a step lifts it and fires `onHoverStep` so the parent
 * can highlight affected assets elsewhere on the page.
 */
import React, { useState } from "react";
import { Box, Typography, Collapse } from "@mui/material";
import { motion } from "framer-motion";
import { ChevronDown } from "lucide-react";
import { T } from "@/assets/theme";

export interface FlowTimelineProps {
  steps: string[];
  color?: string;
  onHoverStep?: (index: number | null) => void;
  /** collapse long step text behind an expander */
  expandable?: boolean;
}

/** Pull a short headline out of a long step sentence. */
function headline(step: string): { head: string; rest: string } {
  const clean = step.replace(/^\d+[.)]\s*/, "");
  const idx = clean.search(/[:.—]/);
  if (idx > 8 && idx < 60) {
    return { head: clean.slice(0, idx), rest: clean.slice(idx + 1).trim() };
  }
  if (clean.length <= 64) return { head: clean, rest: "" };
  return { head: clean.slice(0, 60) + "…", rest: clean };
}

const FlowTimeline: React.FC<FlowTimelineProps> = ({
  steps,
  color = T.blue,
  onHoverStep,
  expandable = true,
}) => {
  const [open, setOpen] = useState<number | null>(null);

  return (
    <Box sx={{ position: "relative", pl: 0.5 }}>
      {steps.map((step, i) => {
        const { head, rest } = headline(step);
        const last = i === steps.length - 1;
        return (
          <Box
            key={i}
            sx={{ display: "flex", gap: 1.75, position: "relative" }}
            onMouseEnter={() => onHoverStep?.(i)}
            onMouseLeave={() => onHoverStep?.(null)}
          >
            {/* node + connector */}
            <Box sx={{ display: "flex", flexDirection: "column", alignItems: "center" }}>
              <motion.div
                initial={{ scale: 0 }}
                whileInView={{ scale: 1 }}
                viewport={{ once: true }}
                transition={{ type: "spring", stiffness: 380, damping: 18, delay: i * 0.12 }}
                style={{
                  width: 14, height: 14, borderRadius: "50%",
                  border: `2px solid ${color}`,
                  background: T.card,
                  boxShadow: `0 0 10px ${color}55`,
                  zIndex: 1, flexShrink: 0, marginTop: 4,
                }}
              />
              {!last && (
                <motion.div
                  initial={{ scaleY: 0 }}
                  whileInView={{ scaleY: 1 }}
                  viewport={{ once: true }}
                  transition={{ duration: 0.45, delay: i * 0.12 + 0.1, ease: "easeOut" }}
                  style={{
                    width: 2, flex: 1, minHeight: 22,
                    background: `linear-gradient(${color}88, ${color}22)`,
                    transformOrigin: "top",
                  }}
                />
              )}
            </Box>

            {/* content */}
            <motion.div
              initial={{ opacity: 0, x: -10 }}
              whileInView={{ opacity: 1, x: 0 }}
              viewport={{ once: true }}
              transition={{ duration: 0.4, delay: i * 0.12, ease: T.ease }}
              style={{ paddingBottom: last ? 0 : 18, flex: 1, minWidth: 0 }}
            >
              <Box
                onClick={() => expandable && rest && setOpen(open === i ? null : i)}
                sx={{
                  display: "flex", alignItems: "flex-start", gap: 1,
                  cursor: expandable && rest ? "pointer" : "default",
                  "&:hover": { "& .eco-step-head": { color: T.text } },
                }}
              >
                <Typography
                  className="eco-step-head"
                  sx={{
                    fontSize: "1.08rem", fontWeight: 600, color: T.textDim,
                    transition: "color 160ms", lineHeight: 1.45, flex: 1,
                  }}
                >
                  {head}
                </Typography>
                {expandable && rest && (
                  <motion.div animate={{ rotate: open === i ? 180 : 0 }}>
                    <ChevronDown size={17} color={T.textMute as string} />
                  </motion.div>
                )}
              </Box>
              {rest && (
                <Collapse in={open === i}>
                  <Typography sx={{ fontSize: "0.97rem", color: T.textMute, mt: 0.75, lineHeight: 1.5 }}>
                    {rest}
                  </Typography>
                </Collapse>
              )}
            </motion.div>
          </Box>
        );
      })}
    </Box>
  );
};

export default FlowTimeline;
