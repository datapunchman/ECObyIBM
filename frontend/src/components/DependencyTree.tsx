import React, { useCallback } from "react";
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  useNodesState,
  useEdgesState,
  addEdge,
  Node,
  Edge,
  Connection,
  BackgroundVariant,
  NodeTypes,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { Box, Typography, Paper, Chip } from "@mui/material";
import type { GraphAsset } from "@/types";

// ── Custom node ──────────────────────────────────────────────────────────────

const SYSTEM_COLORS: Record<string, { bg: string; border: string; text: string }> = {
  database:   { bg: "#f6f2ff", border: "#6929c4", text: "#6929c4" },
  sql:        { bg: "#d9fbfb", border: "#005d5d", text: "#005d5d" },
  databricks: { bg: "#fff2e8", border: "#ff6200", text: "#ff6200" },
  pipeline:   { bg: "#edf5ff", border: "#0043ce", text: "#0043ce" },
  powerbi:    { bg: "#f6f2ff", border: "#8a3ffc", text: "#8a3ffc" },
  api:        { bg: "#d9fbfb", border: "#007d79", text: "#007d79" },
};

interface AssetNodeData extends Record<string, unknown> {
  label: string;
  assetType: string;
  system: string;
}

const AssetNode: React.FC<{ data: AssetNodeData }> = ({ data }) => {
  const cfg = SYSTEM_COLORS[data.system] ?? {
    bg: "#f2f4f8",
    border: "#697077",
    text: "#697077",
  };
  return (
    <Paper
      sx={{
        px: 1.5,
        py: 1,
        bgcolor: cfg.bg,
        border: `1.5px solid ${cfg.border}`,
        borderRadius: 1,
        minWidth: 140,
        maxWidth: 220,
        boxShadow: "none",
      }}
    >
      <Chip
        label={data.assetType.replace("_", " ")}
        size="small"
        sx={{
          mb: 0.5,
          height: 16,
          fontSize: "0.875rem",
          fontWeight: 600,
          bgcolor: cfg.border,
          color: "#fff",
          borderRadius: 0.5,
          letterSpacing: "0.04em",
        }}
      />
      <Typography
        variant="body2"
        sx={{
          fontWeight: 600,
          color: cfg.text,
          fontSize: "1.04rem",
          wordBreak: "break-word",
        }}
      >
        {data.label}
      </Typography>
      <Typography variant="caption" sx={{ color: "#697077", fontSize: "0.9rem" }}>
        {data.system}
      </Typography>
    </Paper>
  );
};

const nodeTypes: NodeTypes = { assetNode: AssetNode };

// ── Asset → React Flow node ──────────────────────────────────────────────────

function assetToNode(asset: GraphAsset, index: number): Node<AssetNodeData> {
  const col = index % 4;
  const row = Math.floor(index / 4);
  return {
    id: asset.id,
    type: "assetNode",
    position: { x: col * 260 + 40, y: row * 140 + 40 },
    data: {
      label: asset.name,
      assetType: asset.asset_type,
      system: asset.system,
    },
  };
}

// ── DependencyTree ───────────────────────────────────────────────────────────

interface DependencyTreeProps {
  assets?: GraphAsset[];
  paths?: string[][];
  height?: number | string;
}

const DependencyTree: React.FC<DependencyTreeProps> = ({
  assets = [],
  paths = [],
  height = 480,
}) => {
  const initialNodes: Node<AssetNodeData>[] = assets.map(assetToNode);

  // Build edges from dependency paths
  const edgeSet = new Set<string>();
  const initialEdges: Edge[] = [];
  for (const path of paths) {
    for (let i = 0; i < path.length - 1; i++) {
      const edgeId = `${path[i]}->${path[i + 1]}`;
      if (!edgeSet.has(edgeId)) {
        edgeSet.add(edgeId);
        initialEdges.push({
          id: edgeId,
          source: path[i],
          target: path[i + 1],
          style: { stroke: "#0f62fe", strokeWidth: 1.5 },
          animated: false,
        });
      }
    }
  }

  const [nodes, , onNodesChange] = useNodesState(initialNodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(initialEdges);
  const onConnect = useCallback(
    (params: Connection) => setEdges((eds) => addEdge(params, eds)),
    [setEdges]
  );

  if (!assets.length) {
    return (
      <Box
        sx={{
          height,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          bgcolor: "#f2f4f8",
          border: "1px dashed #dde1e6",
          borderRadius: 1,
        }}
      >
        <Typography variant="body2" color="text.secondary">
          No graph data — run an analysis first.
        </Typography>
      </Box>
    );
  }

  return (
    <Box
      sx={{
        height,
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
        overflow: "hidden",
        bgcolor: "#ffffff",
      }}
    >
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onConnect={onConnect}
        nodeTypes={nodeTypes}
        fitView
        fitViewOptions={{ padding: 0.2 }}
        attributionPosition="bottom-right"
      >
        <Background
          variant={BackgroundVariant.Dots}
          gap={16}
          size={1}
          color="#e0e0e0"
        />
        <Controls />
        <MiniMap
          nodeColor={(n) => {
            const data = n.data as AssetNodeData;
            return SYSTEM_COLORS[data?.system]?.border ?? "#697077";
          }}
          style={{ border: "1px solid #dde1e6" }}
        />
      </ReactFlow>
    </Box>
  );
};

export default DependencyTree;
