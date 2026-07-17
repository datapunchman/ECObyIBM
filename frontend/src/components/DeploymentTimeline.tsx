import React from "react";
import {
  Paper,
  Box,
  Typography,
  Stepper,
  Step,
  StepLabel,
  StepContent,
} from "@mui/material";
import CheckCircleOutlineIcon from "@mui/icons-material/CheckCircleOutline";

interface DeploymentTimelineProps {
  steps: string[];
  title?: string;
}

const DeploymentTimeline: React.FC<DeploymentTimelineProps> = ({
  steps,
  title = "Deployment Plan",
}) => {
  if (!steps.length) return null;

  return (
    <Paper variant="outlined" sx={{ p: 3, borderColor: "divider" }}>
      <Typography
        variant="overline"
        sx={{ color: "text.secondary", mb: 2, display: "block" }}
      >
        {title}
      </Typography>
      <Stepper orientation="vertical" nonLinear>
        {steps.map((step, idx) => (
          <Step key={idx} active completed={false}>
            <StepLabel
              StepIconComponent={() => (
                <Box
                  sx={{
                    width: 24,
                    height: 24,
                    borderRadius: "50%",
                    bgcolor: "#0f62fe",
                    color: "#fff",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    fontSize: "0.9rem",
                    fontWeight: 700,
                    flexShrink: 0,
                  }}
                >
                  {idx + 1}
                </Box>
              )}
            >
              <Typography variant="body2" sx={{ fontWeight: 500 }}>
                Step {idx + 1}
              </Typography>
            </StepLabel>
            <StepContent>
              <Box display="flex" gap={1} alignItems="flex-start" mb={0.5}>
                <CheckCircleOutlineIcon
                  sx={{ fontSize: 15, color: "#42be65", mt: "3px" }}
                />
                <Typography variant="body2" color="text.secondary">
                  {step.trim()}
                </Typography>
              </Box>
            </StepContent>
          </Step>
        ))}
      </Stepper>
    </Paper>
  );
};

export default DeploymentTimeline;
