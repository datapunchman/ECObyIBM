/**
 * EnterpriseFlow — Enterprise Impact Visualization.
 *
 * Not a graph debugger: a story. The change starts as a single glowing
 * node; a blast-radius pulse traces the pipeline stage by stage
 * (Database → ETL → Semantic Model → Reports → Dashboards). Each stage
 * is one aggregate card until the user drills in — aggregate → type
 * groups → actual assets — so 162 nodes never appear at once.
 *
 * Interactions
 *  • hover any node   → its dependency path to the source glows,
 *                       everything else fades to 15%
 *  • click aggregate  → expands (spring), camera zooms, breadcrumb grows
 *  • click asset      → AI commentary card with real downstream counts
 *  • autoplay         → camera walks the pipeline, narrating each stage
 *  • impact wave      → subtle re-pulse of the spine every few seconds
 *
 * PURE FRONTEND: consumes the same EcoGraph built by buildEcoGraph()
 * from the unchanged /analyze/v2 response. No API or data changes.
 */
import React, {
  useCallback, useEffect, useMemo, useRef, useState,
} from "react";
import {
  ReactFlow, Background, MiniMap,
  Node, Edge, BackgroundVariant, NodeTypes,
  Handle, Position, useReactFlow, ReactFlowProvider,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import {
  Box, Typography, TextField, IconButton, Tooltip, Slider,
} from "@mui/material";
import { motion, AnimatePresence } from "framer-motion";
import {
  Play, Square, Crosshair, RotateCcw, Database as DbIcon,
  Workflow, Layers, BarChart3, LayoutDashboard, Zap, ChevronRight,
  Maximize2, Minimize2,
} from "lucide-react";
import { T } from "@/assets/theme";
import type { V2AnalysisResult, V2ImpactedAsset } from "@/types";

/* ════════════════════════════════════════════════════════════════ */
/* Graph model (unchanged API)                                       */
/* ════════════════════════════════════════════════════════════════ */

export interface EcoGraph {
  nodes: EcoNode[];
  edges: { source: string; target: string }[];
  sourceId: string | null;
  adj: Map<string, string[]>;
  depth: Map<string, number>;
}

export interface EcoNode {
  id: string;
  name: string;
  /** backend asset_type verbatim; "unknown" when the id appears only in
      dependency_paths and no bucket entry describes it */
  type: string;
  /** backend system verbatim; "unknown" when absent */
  system: string;
  /** backend bucket verbatim; "" when unbucketed */
  bucket: string;
}

const BUCKET_KEYS = [
  "database_tables", "views", "materialized_views", "stored_procedures",
  "functions", "databricks_notebooks", "spark_jobs", "delta_live_tables",
  "unity_catalog", "pipelines", "data_factory", "airflow", "fabric_pipelines",
  "adls_files", "semantic_models", "powerbi_reports", "dashboards", "apis", "external_consumers",
] as const;

/** Power BI internal date scaffolding — the backend filters these from the
    asset list (metadata/loader.py) but they still leak through
    dependency_paths; hide them here too. */
const INTERNAL_ASSET_RE = /(^|::)(LocalDateTable_|DateTableTemplate_)/;

function shortName(id: string): string {
  const parts = id.split("::");
  return parts[parts.length - 1] || id;
}

export function buildEcoGraph(result: V2AnalysisResult): EcoGraph {
  const ga = result.graph_analysis;
  const sourceId = result.source_asset.id;

  const assetIndex = new Map<string, V2ImpactedAsset>();
  for (const key of BUCKET_KEYS) {
    for (const a of (ga[key] ?? []) as V2ImpactedAsset[]) assetIndex.set(a.id, a);
  }

  const nodeIds = new Set<string>();
  const edgeSet = new Set<string>();
  const edges: { source: string; target: string }[] = [];
  const adj = new Map<string, string[]>();
  const depth = new Map<string, number>();

  /* Edges come verbatim from dependency_paths — direction preserved
     exactly as received, no reversal or inference. Hops through internal
     Power BI tables are dropped (node + its edges). */
  for (const path of ga.dependency_paths ?? []) {
    const clean = path.filter((id) => !INTERNAL_ASSET_RE.test(id));
    clean.forEach((id, i) => {
      nodeIds.add(id);
      depth.set(id, Math.min(depth.get(id) ?? Infinity, i));
      if (i < clean.length - 1) {
        const key = `${id}->${clean[i + 1]}`;
        if (!edgeSet.has(key)) {
          edgeSet.add(key);
          edges.push({ source: id, target: clean[i + 1] });
          const list = adj.get(id) ?? [];
          list.push(clean[i + 1]);
          adj.set(id, list);
        }
      }
    });
  }

  /* Bucketed assets that never appear in any path are still impacted per
     the backend — include the NODE, but do NOT fabricate an edge for it
     (the previous source→asset synthetic edges were frontend invention). */
  for (const [id] of assetIndex) {
    if (INTERNAL_ASSET_RE.test(id)) continue;
    if (!nodeIds.has(id)) {
      nodeIds.add(id);
      depth.set(id, 1);
    }
  }
  if (sourceId) { nodeIds.add(sourceId); depth.set(sourceId, 0); }

  const nodes: EcoNode[] = [...nodeIds].map((id) => {
    const a = assetIndex.get(id);
    const isSource = id === sourceId;
    return {
      id,
      name: a?.asset ?? (isSource ? result.source_asset.name ?? shortName(id) : shortName(id)),
      type: a?.type ?? (isSource ? result.source_asset.type ?? "unknown" : "unknown"),
      system: a?.system ?? (isSource ? result.source_asset.system ?? "unknown" : "unknown"),
      bucket: a?.bucket ?? "",
    };
  });

  return { nodes, edges, sourceId, adj, depth };
}

/* ════════════════════════════════════════════════════════════════ */
/* Pipeline stages — display columns keyed by BACKEND bucket names.  */
/* No type/name regexes: an asset's stage is a direct lookup of the  */
/* bucket string the API assigned it.                                */
/* ════════════════════════════════════════════════════════════════ */

const STAGES = [
  { key: "source",    label: "Source",          hue: T.cyan,     icon: Zap,             buckets: [] as string[] },
  { key: "database",  label: "Database",        hue: T.purple,   icon: DbIcon,
    buckets: ["database_tables", "views", "materialized_views", "stored_procedures", "functions"] },
  { key: "etl",       label: "ETL & Pipelines", hue: "#F97316",  icon: Workflow,
    buckets: ["databricks_notebooks", "spark_jobs", "delta_live_tables", "unity_catalog",
              "pipelines", "data_factory", "airflow", "fabric_pipelines", "adls_files",
              "apis", "external_consumers"] },
  { key: "semantic",  label: "Semantic Model",  hue: T.blueSoft, icon: Layers,
    buckets: ["semantic_models"] },
  { key: "reports",   label: "Reports",         hue: "#E879F9",  icon: BarChart3,
    buckets: ["powerbi_reports"] },
  { key: "dash",      label: "Dashboards",      hue: T.danger,   icon: LayoutDashboard,
    buckets: ["dashboards"] },
  { key: "unclassified", label: "Unclassified", hue: "#64748B",  icon: Layers,
    buckets: [] as string[] },
] as const;

const UNCLASSIFIED_STAGE = STAGES.length - 1;

const BUCKET_TO_STAGE: Map<string, number> = new Map(
  STAGES.flatMap((s, i) => s.buckets.map((b) => [b, i] as [string, number]))
);

const DATABASE_BUCKETS: Set<string> = new Set(STAGES[1].buckets);

function stageOfNode(n: EcoNode, sourceId: string | null): number {
  if (n.id === sourceId) return 0;
  /* Known backend defect: powerbi-system assets (e.g. the TMDL _Measures
     container table) arrive bucketed under database_tables. Per backend
     `system`, render them under Semantic Model — never inside Database. */
  if (n.system === "powerbi" && DATABASE_BUCKETS.has(n.bucket)) return 3;
  const s = BUCKET_TO_STAGE.get(n.bucket);
  /* Unbucketed (path-only) assets are shown as Unclassified rather than
     silently dropped or guessed into a system they may not belong to. */
  return s ?? UNCLASSIFIED_STAGE;
}

type Expansion = "collapsed" | "groups" | "assets";

interface StageModel {
  index: number;
  nodes: EcoNode[];
  groups: Map<string, EcoNode[]>; // backend asset_type → nodes
}

function buildStages(graph: EcoGraph): StageModel[] {
  const out: StageModel[] = STAGES.map((_, i) => ({
    index: i, nodes: [], groups: new Map(),
  }));
  for (const n of graph.nodes) {
    const s = stageOfNode(n, graph.sourceId);
    out[s].nodes.push(n);
    const t = n.type.replace(/_/g, " ");
    const g = out[s].groups.get(t) ?? [];
    g.push(n);
    out[s].groups.set(t, g);
  }
  return out;
}

function downstreamCount(id: string, adj: Map<string, string[]>): number {
  const seen = new Set<string>([id]);
  const q = [id];
  while (q.length) {
    const c = q.shift()!;
    for (const nx of adj.get(c) ?? []) if (!seen.has(nx)) { seen.add(nx); q.push(nx); }
  }
  return seen.size - 1;
}

/* ════════════════════════════════════════════════════════════════ */
/* Custom nodes                                                      */
/* ════════════════════════════════════════════════════════════════ */

interface StoryNodeData extends Record<string, unknown> {
  kind: "source" | "hub" | "hubHeader" | "group" | "asset" | "more";
  label: string;
  sub?: string;
  count?: number;
  hue: string;
  Icon?: React.ElementType;
  dim: boolean;
  lit: boolean;
  wave?: boolean;
}

/**
 * Node shell — deliberately NOT a Framer mount animation.
 * React Flow recreates node internals on every graph rebuild (hover
 * re-paints), so an `initial={{scale:0}}` entrance replays constantly
 * and the node shrinks out from under the cursor → mouseleave →
 * re-enter → vigorous flicker. Plain CSS transitions are stable.
 * Hover feedback is glow-only (no transform) so hit-testing geometry
 * never changes under the pointer.
 */
const nodeShell = (data: StoryNodeData, children: React.ReactNode, extra?: object) => (
  <Box style={{ position: "relative", opacity: data.dim ? 0.15 : 1, transition: "opacity 300ms" }}>
    <Handle type="target" position={Position.Left} style={{ opacity: 0 }} />
    <Handle type="source" position={Position.Right} style={{ opacity: 0 }} />
    <Box className="eco-card" sx={{
      borderRadius: "12px",
      background: data.lit
        ? `linear-gradient(150deg, ${data.hue}30, rgba(17,24,39,0.94))`
        : "rgba(17,24,39,0.9)",
      border: `1px solid ${data.lit ? data.hue : `${data.hue}44`}`,
      backdropFilter: "blur(10px)",
      boxShadow: data.lit
        ? `0 0 26px ${data.hue}66, 0 6px 18px rgba(2,6,17,0.5)`
        : `0 0 12px ${data.hue}18, 0 4px 14px rgba(2,6,17,0.45)`,
      transition: "box-shadow 300ms, border-color 300ms, background 300ms",
      "&:hover": { boxShadow: `0 0 34px ${data.hue}88`, borderColor: data.hue },
      ...extra,
    }}>
      {children}
    </Box>
  </Box>
);

const SourceNode: React.FC<{ data: StoryNodeData }> = ({ data }) => (
  <Box sx={{ position: "relative" }}>
    {/* blast pulse rings */}
    {data.wave && [0, 1].map((i) => (
      <Box key={i} sx={{
        position: "absolute", inset: -6, borderRadius: "16px",
        border: `1.5px solid ${T.cyan}`,
        animation: `eco-blast 2.4s ease-out ${i * 1.2}s infinite`,
        pointerEvents: "none",
      }} />
    ))}
    {nodeShell(data, (
      <Box sx={{ px: 2.25, py: 1.5, minWidth: 220, textAlign: "center" }}>
        <Typography sx={{
          fontSize: "0.875rem", fontWeight: 700, letterSpacing: "0.16em",
          textTransform: "uppercase", color: T.cyan, mb: 0.5,
        }}>
          Change origin
        </Typography>
        <Typography sx={{
          fontFamily: T.mono, fontSize: "1.41rem", fontWeight: 700, color: T.text,
          textShadow: `0 0 20px ${T.cyan}88`,
        }}>
          {data.label}
        </Typography>
      </Box>
    ))}
  </Box>
);

const HubNode: React.FC<{ data: StoryNodeData }> = ({ data }) => {
  const Icon = data.Icon ?? Layers;
  return nodeShell(data, (
    <Box sx={{ px: 2, py: 1.5, minWidth: 218, textAlign: "center", cursor: "pointer" }}>
      <Icon size={24} color={data.hue} style={{ marginBottom: 6 }} />
      <Typography sx={{ fontSize: "2.37rem", fontWeight: 700, color: T.text, lineHeight: 1 }}>
        {data.count}
      </Typography>
      <Typography sx={{
        fontSize: "0.92rem", fontWeight: 700, letterSpacing: "0.12em",
        textTransform: "uppercase", color: data.hue, mt: 0.6,
      }}>
        {data.label}
      </Typography>
      <Typography sx={{ fontSize: "0.875rem", color: T.textMute, mt: 0.3 }}>
        impacted · click to expand
      </Typography>
    </Box>
  ));
};

const HubHeaderNode: React.FC<{ data: StoryNodeData }> = ({ data }) => {
  const Icon = data.Icon ?? Layers;
  return nodeShell(data, (
    <Box sx={{ px: 1.5, py: 0.75, display: "flex", alignItems: "center", gap: 1, cursor: "pointer" }}>
      <Icon size={18} color={data.hue} />
      <Typography sx={{
        fontSize: "0.95rem", fontWeight: 700, letterSpacing: "0.1em",
        textTransform: "uppercase", color: data.hue,
      }}>
        {data.label} · {data.count}
      </Typography>
    </Box>
  ), { borderRadius: "8px" });
};

const GroupNode: React.FC<{ data: StoryNodeData }> = ({ data }) => nodeShell(data, (
  <Box sx={{ px: 1.75, py: 1, minWidth: 210, display: "flex", alignItems: "center", gap: 1.25, cursor: "pointer" }}>
    <Box sx={{ width: 7, height: 7, borderRadius: "50%", bgcolor: data.hue, boxShadow: `0 0 8px ${data.hue}` }} />
    <Box>
      <Typography sx={{ fontSize: "1.13rem", fontWeight: 700, color: T.text, textTransform: "capitalize" }}>
        {data.label}
      </Typography>
      <Typography sx={{ fontSize: "0.9rem", color: T.textMute }}>
        {data.count} assets
      </Typography>
    </Box>
  </Box>
));

const AssetNode: React.FC<{ data: StoryNodeData }> = ({ data }) => {
  /* Pure renderer: no invented risk levels — /analyze/v2 exposes no
     per-asset criticality field, so none is displayed. */
  return nodeShell(data, (
    <Box sx={{ px: 1.5, py: 0.9, minWidth: 220, maxWidth: 250 }}>
      <Box sx={{ display: "flex", alignItems: "center", gap: 0.75 }}>
        <Box sx={{
          width: 6, height: 6, borderRadius: "50%", bgcolor: data.hue, flexShrink: 0,
          boxShadow: `0 0 6px ${data.hue}`,
        }} />
        <Typography sx={{
          fontSize: "1.09rem", fontWeight: 600, color: T.text, lineHeight: 1.3,
          overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
        }}>
          {data.label}
        </Typography>
      </Box>
      <Box sx={{ display: "flex", alignItems: "center", gap: 0.75, mt: 0.35 }}>
        <Typography sx={{ fontSize: "0.875rem", color: T.textMute, textTransform: "capitalize" }}>
          {data.sub}
        </Typography>
      </Box>
    </Box>
  ));
};

const MoreNode: React.FC<{ data: StoryNodeData }> = ({ data }) => nodeShell(data, (
  <Box sx={{ px: 1.5, py: 0.6, cursor: "pointer" }}>
    <Typography sx={{ fontSize: "0.97rem", color: T.textDim, fontWeight: 600 }}>
      +{data.count} more…
    </Typography>
  </Box>
), { borderRadius: "8px", borderStyle: "dashed" });

const nodeTypes: NodeTypes = {
  source: SourceNode, hub: HubNode, hubHeader: HubHeaderNode,
  group: GroupNode, asset: AssetNode, more: MoreNode,
};

/* ════════════════════════════════════════════════════════════════ */
/* Main component                                                    */
/* ════════════════════════════════════════════════════════════════ */

export interface EnterpriseFlowProps {
  graph: EcoGraph;
  height?: number | string;
  toolbar?: boolean;
  highlightSystems?: string[] | null;
}

const COL_X = 420;
const ASSET_CAP = 8;

const STATUS_STEPS = [
  "Metadata Loaded", "Graph Traversed", "Impact Calculated", "Granite Analysis Complete",
];

interface Commentary {
  title: string;
  hue: string;
  lines: [string, string][];
}

const SYS_TO_STAGES: Record<string, number[]> = {
  database: [1], sql: [1], databricks: [2], pipeline: [2], api: [2],
  powerbi: [3, 4, 5],
};

const InnerFlow: React.FC<EnterpriseFlowProps> = ({
  graph, height = 520, toolbar = true, highlightSystems = null,
}) => {
  const stages = useMemo(() => buildStages(graph), [graph]);
  const presentStages = useMemo(
    () => stages.filter((s) => s.nodes.length > 0).map((s) => s.index),
    [stages]
  );

  const [reveal, setReveal] = useState(0);           // blast radius progress
  const [tracing, setTracing] = useState(true);      // intro text
  const [expansion, setExpansion] = useState<Record<number, Expansion>>({});
  const [openGroups, setOpenGroups] = useState<Set<string>>(new Set());
  const [caps, setCaps] = useState<Record<string, number>>({});
  const [selected, setSelected] = useState<Commentary | null>(null);
  const [crumbs, setCrumbs] = useState<string[]>([]);
  const [search, setSearch] = useState("");
  const [maxStage, setMaxStage] = useState(UNCLASSIFIED_STAGE);
  const [statusCount, setStatusCount] = useState(0);
  const [playing, setPlaying] = useState(false);
  /* incremented whenever a layout change should re-fit the camera;
     the effect below runs AFTER React Flow has measured the new nodes —
     calling fitView on a click timer raced measurement and flew the
     camera to unmeasured coordinates (blank screen). */
  const [fitRequest, setFitRequest] = useState(0);
  const playTimers = useRef<number[]>([]);
  const { fitView } = useReactFlow();

  const sourceName = useMemo(
    () => graph.nodes.find((n) => n.id === graph.sourceId)?.name ?? "source",
    [graph]
  );

  /* ── blast radius on mount ── */
  useEffect(() => {
    const timers: number[] = [];
    timers.push(window.setTimeout(() => setTracing(true), 400));
    presentStages.forEach((s, i) => {
      timers.push(window.setTimeout(() => {
        setReveal(s);
        if (i === presentStages.length - 1) setTracing(false);
      }, 1000 + i * 380));
    });
    STATUS_STEPS.forEach((_, i) => {
      timers.push(window.setTimeout(() => setStatusCount(i + 1), 700 + i * 450));
    });
    return () => timers.forEach(clearTimeout);
  }, [presentStages]);

  /* ── periodic impact wave (imperative — pulses the spine edges via
        CSS class toggling; touching React state here rebuilt the whole
        node array every 7s and re-triggered mount flicker) ── */
  const wrapRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const iv = window.setInterval(() => {
      const paths = wrapRef.current?.querySelectorAll<SVGPathElement>(".eco-edge-flow .react-flow__edge-path");
      paths?.forEach((p) => {
        p.style.strokeWidth = "2.4";
        window.setTimeout(() => { p.style.strokeWidth = ""; }, 1200);
      });
    }, 7000);
    return () => window.clearInterval(iv);
  }, []);

  /* ── build visible nodes + edges from story state ──
     NOTE: hover is deliberately NOT an input here. Hover-path highlight
     is applied imperatively (CSS classes on the rendered DOM) in the
     effect below — rebuilding this array on mouseenter re-rendered every
     node and caused the flicker. */
  const { nodes, edges } = useMemo(() => {
    const ns: Node<StoryNodeData>[] = [];
    const es: Edge[] = [];
    const q = search.trim().toLowerCase();

    const hubId = (s: number) => `hub-${s}`;
    /* external highlight only (e.g. Analysis-page bubble/timeline hover) */
    let litSet: Set<string> | null = null;
    if (highlightSystems) {
      litSet = new Set(["src"]);
      for (const sys of highlightSystems) {
        for (const s of SYS_TO_STAGES[sys] ?? []) litSet.add(hubId(s));
      }
    }

    const state = (id: string): { dim: boolean; lit: boolean } => ({
      dim: litSet ? !litSet.has(id) : false,
      lit: litSet ? litSet.has(id) : false,
    });

    /* source */
    if (graph.sourceId) {
      ns.push({
        id: "src", type: "source", position: { x: 0, y: -32 },
        draggable: false,
        data: {
          kind: "source", label: sourceName, hue: T.cyan,
          wave: true, ...state("src"),
        },
      });
    }

    let prevAnchor = "src";
    for (const s of presentStages) {
      if (s === 0) continue;
      if (s > Math.min(reveal, maxStage)) break;
      const stage = stages[s];
      const meta = STAGES[s];
      const exp: Expansion = q ? "assets" : (expansion[s] ?? "collapsed");
      const x = presentStages.filter((p) => p !== 0 && p <= s).length * COL_X;

      if (exp === "collapsed") {
        const id = hubId(s);
        ns.push({
          id, type: "hub", position: { x, y: -46 }, draggable: false,
          data: {
            kind: "hub", label: meta.label, hue: meta.hue, Icon: meta.icon,
            count: stage.nodes.length,
            ...state(id),
          },
        });
        es.push(spineEdge(prevAnchor, id, meta.hue, litSet));
        prevAnchor = id;
      } else {
        /* expanded: header chip + stacked items */
        const headerId = hubId(s);
        const items: Node<StoryNodeData>[] = [];
        let y = 0;

        const groupEntries = [...stage.groups.entries()];
        const skipGroups = groupEntries.length === 1 || stage.nodes.length <= 6;

        for (const [gType, gNodes] of groupEntries) {
          const gid = `grp-${s}-${gType}`;
          const groupOpen = skipGroups || exp === "assets" && (openGroups.has(gid) || q !== "");
          if (!skipGroups) {
            items.push({
              id: gid, type: "group", position: { x, y }, draggable: false,
              data: {
                kind: "group", label: gType, hue: meta.hue, count: gNodes.length,
                ...state(gid),
              },
            });
            es.push(fanEdge(headerId, gid, meta.hue, litSet));
            y += 84;
          }
          if (groupOpen) {
            const cap = caps[gid] ?? ASSET_CAP;
            const filtered = gNodes.filter((n) =>
              (!q || n.name.toLowerCase().includes(q))
            );
            filtered.slice(0, cap).forEach((n) => {
              items.push({
                id: n.id, type: "asset",
                position: { x: x + (skipGroups ? 0 : 26), y }, draggable: false,
                data: {
                  kind: "asset", label: n.name, sub: n.type.replace(/_/g, " "),
                  hue: meta.hue, ...state(n.id),
                },
              });
              es.push(fanEdge(skipGroups ? headerId : gid, n.id, meta.hue, litSet));
              y += 78;
            });
            if (filtered.length > cap) {
              const moreId = `more-${gid}`;
              items.push({
                id: moreId, type: "more",
                position: { x: x + (skipGroups ? 0 : 26), y }, draggable: false,
                data: {
                  kind: "more", label: "", hue: meta.hue,
                  count: filtered.length - cap, dim: false, lit: false,
                },
              });
              y += 62;
            }
            y += 8;
          }
        }

        const totalH = y;
        /* header chip above the stack, stack vertically centred */
        const offset = -totalH / 2;
        items.forEach((it) => { it.position.y += offset; });
        ns.push({
          id: headerId, type: "hubHeader",
          position: { x, y: offset - 52 }, draggable: false,
          data: {
            kind: "hubHeader", label: meta.label, hue: meta.hue, Icon: meta.icon,
            count: stage.nodes.length, ...state(headerId),
          },
        });
        ns.push(...items);
        es.push(spineEdge(prevAnchor, headerId, meta.hue, litSet));
        prevAnchor = headerId;
      }
    }

    return { nodes: ns, edges: es };
  }, [
    graph, stages, presentStages, reveal, maxStage, expansion, openGroups,
    caps, highlightSystems, search, sourceName,
  ]);

  /* ── edge builders ── */
  function spineEdge(from: string, to: string, hue: string, lit: Set<string> | null): Edge {
    const isLit = lit ? lit.has(from) && lit.has(to) : false;
    const dimmed = lit ? !isLit : false;
    return {
      id: `sp-${from}->${to}`, source: from, target: to,
      className: "eco-edge-flow",
      style: {
        stroke: isLit ? T.cyan : dimmed ? "rgba(77,163,255,0.08)" : `${hue}88`,
        strokeWidth: isLit ? 2.6 : 1.6,
        filter: isLit ? `drop-shadow(0 0 5px ${T.cyan})` : `drop-shadow(0 0 3px ${hue}44)`,
        transition: "stroke 300ms, stroke-width 600ms",
      },
    };
  }
  function fanEdge(from: string, to: string, hue: string, lit: Set<string> | null): Edge {
    const isLit = lit ? lit.has(to) : false;
    const dimmed = lit ? !isLit : false;
    return {
      id: `fn-${from}->${to}`, source: from, target: to,
      style: {
        stroke: isLit ? T.cyan : dimmed ? "rgba(148,163,184,0.05)" : `${hue}3d`,
        strokeWidth: isLit ? 2 : 1,
        transition: "stroke 300ms",
      },
    };
  }

  /* ── interactions ── */
  const zoomToStage = useCallback((s: number) => {
    window.setTimeout(() => {
      fitView({
        nodes: nodes.filter((n) => n.id === `hub-${s}` || n.position.x === 0 || true).map((n) => ({ id: n.id })),
        duration: 650, padding: 0.25,
      });
    }, 80);
  }, [fitView, nodes]);

  const describeAsset = useCallback((eco: EcoNode): Commentary => {
    const ds = downstreamCount(eco.id, graph.adj);
    const meta = STAGES[stageOfNode(eco, graph.sourceId)];
    return {
      title: eco.name,
      hue: meta.hue,
      lines: [
        ["Type", eco.type.replace(/_/g, " ")],
        ["System", eco.system],
        ["Downstream", `${ds} assets`],
        ["Bucket", eco.bucket || "(path-only)"],
      ],
    };
  }, [graph]);

  const onNodeClick = useCallback((_: React.MouseEvent, node: Node) => {
    const id = node.id;
    const hubM = id.match(/^hub-(\d+)$/);
    if (hubM) {
      const s = Number(hubM[1]);
      const cur = expansion[s] ?? "collapsed";
      const next: Expansion = cur === "collapsed"
        ? (stages[s].groups.size === 1 || stages[s].nodes.length <= 6 ? "assets" : "groups")
        : "collapsed";
      setExpansion((e) => ({ ...e, [s]: next }));
      if (next !== "collapsed") {
        setCrumbs((c) => [...c.filter((x) => x !== STAGES[s].label), STAGES[s].label]);
      } else {
        setCrumbs((c) => c.filter((x) => x !== STAGES[s].label));
      }
      setFitRequest((f) => f + 1);
      return;
    }
    const grpM = id.match(/^grp-(\d+)-(.+)$/);
    if (grpM) {
      const s = Number(grpM[1]);
      setExpansion((e) => ({ ...e, [s]: "assets" }));
      setOpenGroups((g) => {
        const n = new Set(g);
        if (n.has(id)) n.delete(id); else n.add(id);
        return n;
      });
      setCrumbs((c) => [...c.filter((x) => x !== grpM[2]), grpM[2]]);
      setFitRequest((f) => f + 1);
      return;
    }
    if (id.startsWith("more-")) {
      const gid = id.replace(/^more-/, "");
      setCaps((c) => ({ ...c, [gid]: (c[gid] ?? ASSET_CAP) + 12 }));
      setFitRequest((f) => f + 1);
      return;
    }
    if (id === "src") {
      setSelected({
        title: sourceName, hue: T.cyan,
        lines: [
          ["Role", "change origin"],
          ["Blast radius", `${graph.nodes.length - 1} assets`],
          ["Stages reached", `${presentStages.length - 1}`],
        ],
      });
      return;
    }
    const eco = graph.nodes.find((n) => n.id === id);
    if (eco) setSelected(describeAsset(eco));
  }, [expansion, stages, graph, fitView, describeAsset, sourceName, presentStages]);

  /* ── imperative hover-path highlight ──────────────────────────────
     Zero React re-renders on hover: we toggle .eco-lit / .eco-dim CSS
     classes directly on the already-rendered React Flow node/edge DOM.
     The previous approach (hover → setState → rebuild node array)
     re-rendered every node on mouseenter and caused violent flicker. */
  const clearHoverClasses = useCallback(() => {
    wrapRef.current
      ?.querySelectorAll(".eco-lit, .eco-dim")
      .forEach((el) => el.classList.remove("eco-lit", "eco-dim"));
  }, []);

  const applyHover = useCallback((id: string) => {
    const root = wrapRef.current;
    if (!root) return;
    // resolve the spine chain from source up to the hovered element's stage
    const lit = new Set<string>([id, "src"]);
    const m = id.match(/^hub-(\d+)$/) ?? id.match(/^grp-(\d+)-/);
    let hs: number | null = m ? Number(m[1]) : null;
    let eco: EcoNode | undefined;
    if (hs === null) {
      eco = graph.nodes.find((n) => n.id === id);
      if (eco) hs = stageOfNode(eco, graph.sourceId);
    }
    if (hs !== null) {
      for (const s of presentStages) if (s !== 0 && s <= hs) lit.add(`hub-${s}`);
      if (eco) lit.add(`grp-${hs}-${eco.type.replace(/_/g, " ")}`);
    }
    root.querySelectorAll<HTMLElement>(".react-flow__node").forEach((el) => {
      const nid = el.getAttribute("data-id") ?? "";
      el.classList.toggle("eco-lit", lit.has(nid));
      el.classList.toggle("eco-dim", !lit.has(nid));
    });
    root.querySelectorAll<HTMLElement>(".react-flow__edge").forEach((el) => {
      const eid = el.getAttribute("data-id") ?? "";
      // edge ids look like "sp-a->b" / "fn-a->b"
      const mm = eid.match(/^(?:sp|fn)-(.+)->(.+)$/);
      const on = !!mm && lit.has(mm[1]) && lit.has(mm[2]);
      el.classList.toggle("eco-lit", on);
      el.classList.toggle("eco-dim", !on);
    });
  }, [graph, presentStages]);

  const onNodeMouseEnter = useCallback((_: React.MouseEvent, n: Node) => {
    applyHover(n.id);
  }, [applyHover]);

  const onNodeMouseLeave = useCallback(() => {
    clearHoverClasses();
  }, [clearHoverClasses]);

  const onPaneClick = useCallback(() => {
    setSelected(null);
    clearHoverClasses();
  }, [clearHoverClasses]);

  const replayBlast = useCallback(() => {
    setReveal(0);
    setExpansion({});
    setOpenGroups(new Set());
    setCrumbs([]);
    setSelected(null);
    presentStages.forEach((s, i) => {
      window.setTimeout(() => setReveal(s), 350 + i * 380);
    });
    window.setTimeout(() => fitView({ duration: 700, padding: 0.2 }), 350 + presentStages.length * 380);
  }, [presentStages, fitView]);

  const expandAll = useCallback(() => {
    const exp: Record<number, Expansion> = {};
    const groups = new Set<string>();
    for (const s of presentStages) {
      if (s === 0) continue;
      exp[s] = "assets";
      for (const [gType] of stages[s].groups) groups.add(`grp-${s}-${gType}`);
    }
    setExpansion(exp);
    setOpenGroups(groups);
    setFitRequest((f) => f + 1);
  }, [presentStages, stages]);

  const collapseAll = useCallback(() => {
    setExpansion({});
    setOpenGroups(new Set());
    setCrumbs([]);
    setSelected(null);
    setFitRequest((f) => f + 1);
  }, []);

  /* ── autoplay: AI presents each stage ── */
  const stopPlay = useCallback(() => {
    playTimers.current.forEach(clearTimeout);
    playTimers.current = [];
    setPlaying(false);
    setSelected(null);
    fitView({ duration: 600, padding: 0.2 });
  }, [fitView]);

  const autoplay = useCallback(() => {
    if (playing) { stopPlay(); return; }
    setPlaying(true);
    const seq = presentStages.filter((s) => s !== 0);
    seq.forEach((s, i) => {
      playTimers.current.push(window.setTimeout(() => {
        const meta = STAGES[s];
        const stage = stages[s];
        const top = stage.nodes.slice(0, 3).map((n) => n.name).join(", ");
        const typeSummary = [...stage.groups.entries()]
          .map(([t, l]) => `${l.length} ${t}`).join(", ");
        setSelected({
          title: meta.label, hue: meta.hue,
          lines: [
            ["Impacted", `${stage.nodes.length} assets`],
            ["Types", typeSummary || "—"],
            ["Examples", top || "—"],
          ],
        });
        const ids = ["src", `hub-${s}`];
        fitView({ nodes: ids.map((id) => ({ id })), duration: 700, padding: 0.35 });
      }, i * 2200));
    });
    playTimers.current.push(window.setTimeout(() => {
      stopPlay();
    }, seq.length * 2200 + 600));
  }, [playing, presentStages, stages, fitView, stopPlay]);

  useEffect(() => () => playTimers.current.forEach(clearTimeout), []);

  /* refit when reveal grows */
  useEffect(() => {
    if (reveal > 0) fitView({ duration: 550, padding: 0.22 });
  }, [reveal, fitView]);

  /* deferred camera fit: runs after the rebuilt nodes have been rendered
     and measured (double rAF), so fitView never targets unmeasured
     coordinates. Fixes the blank-screen on expand/collapse. */
  useEffect(() => {
    if (fitRequest === 0) return;
    clearHoverClasses(); // node set changed — drop stale lit/dim classes
    let raf2 = 0;
    const raf1 = requestAnimationFrame(() => {
      raf2 = requestAnimationFrame(() => {
        fitView({ duration: 600, padding: 0.2 });
      });
    });
    return () => { cancelAnimationFrame(raf1); cancelAnimationFrame(raf2); };
  }, [fitRequest, fitView, clearHoverClasses]);

  void zoomToStage;

  return (
    <Box ref={wrapRef} sx={{ position: "relative", height, borderRadius: "16px", overflow: "hidden", ...T.glass }}>
      {/* keyframes local to this viz */}
      <Box component="style">{`
        @keyframes eco-blast {
          0% { transform: scale(1); opacity: 0.8; }
          100% { transform: scale(2.2); opacity: 0; }
        }
        @keyframes eco-breathe-red {
          0%, 100% { box-shadow: 0 0 12px rgba(239,68,68,0.15); }
          50% { box-shadow: 0 0 26px rgba(239,68,68,0.45); }
        }
      `}</Box>

      {/* ── toolbar ── */}
      {toolbar && (
        <Box sx={{
          position: "absolute", top: 12, left: 12, right: 12, zIndex: 10,
          display: "flex", gap: 1, alignItems: "center", flexWrap: "wrap",
          p: 1, borderRadius: "12px",
          background: "rgba(7,11,20,0.72)", backdropFilter: "blur(14px)",
          border: `1px solid ${T.border}`,
        }}>
          <TextField
            size="small" placeholder="Search assets…" value={search}
            onChange={(e) => setSearch(e.target.value)}
            sx={{ width: 210, "& .MuiOutlinedInput-root": { fontSize: "1.05rem", height: 40, borderRadius: "8px" } }}
          />
          {/* "Critical only" filter removed: /analyze/v2 exposes no
              per-asset criticality field — a filter would be invented. */}
          <Box sx={{ display: "flex", alignItems: "center", gap: 1, px: 1, minWidth: 130 }}>
            <Typography sx={{ fontSize: "1.0rem", color: T.textMute, whiteSpace: "nowrap" }}>
              Depth {maxStage >= UNCLASSIFIED_STAGE ? "max" : maxStage}
            </Typography>
            <Slider
              size="small" min={1} max={UNCLASSIFIED_STAGE} value={maxStage}
              onChange={(_, v) => setMaxStage(v as number)}
              sx={{ width: 70, color: T.cyan }}
            />
          </Box>
          <Box sx={{ flex: 1 }} />
          <Tooltip title={playing ? "Stop tour" : "AI walkthrough"}>
            <IconButton size="small" onClick={autoplay} sx={{
              color: playing ? T.danger : T.cyan,
              border: `1px solid ${T.borderUp}`, borderRadius: "8px",
              "&:hover": { boxShadow: `0 0 12px ${T.cyan}44` },
            }}>
              {playing ? <Square size={17} /> : <Play size={18} />}
            </IconButton>
          </Tooltip>
          <Tooltip title="Replay blast radius">
            <IconButton size="small" onClick={replayBlast}
              sx={{ color: T.blueSoft, border: `1px solid ${T.borderUp}`, borderRadius: "8px" }}>
              <Zap size={18} />
            </IconButton>
          </Tooltip>
          <Tooltip title="Expand all">
            <IconButton size="small" onClick={expandAll}
              sx={{ color: T.textDim, border: `1px solid ${T.borderUp}`, borderRadius: "8px" }}>
              <Maximize2 size={17} />
            </IconButton>
          </Tooltip>
          <Tooltip title="Collapse all">
            <IconButton size="small" onClick={collapseAll}
              sx={{ color: T.textDim, border: `1px solid ${T.borderUp}`, borderRadius: "8px" }}>
              <Minimize2 size={17} />
            </IconButton>
          </Tooltip>
          <Tooltip title="Fit view">
            <IconButton size="small" onClick={() => fitView({ duration: 500, padding: 0.2 })}
              sx={{ color: T.textDim, border: `1px solid ${T.borderUp}`, borderRadius: "8px" }}>
              <Crosshair size={18} />
            </IconButton>
          </Tooltip>
          <Tooltip title="Reset">
            <IconButton size="small" onClick={() => { setExpansion({}); setOpenGroups(new Set()); setCrumbs([]); setSelected(null); setSearch(""); }}
              sx={{ color: T.textDim, border: `1px solid ${T.borderUp}`, borderRadius: "8px" }}>
              <RotateCcw size={18} />
            </IconButton>
          </Tooltip>
        </Box>
      )}

      {/* ── breadcrumb ── */}
      <AnimatePresence>
        {crumbs.length > 0 && (
          <motion.div
            initial={{ opacity: 0, y: -8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}
            style={{ position: "absolute", top: toolbar ? 62 : 12, left: 14, zIndex: 10 }}
          >
            <Box sx={{
              display: "flex", alignItems: "center", gap: 0.5, px: 1.5, py: 0.6,
              borderRadius: "8px", background: "rgba(7,11,20,0.7)", backdropFilter: "blur(10px)",
              border: `1px solid ${T.border}`,
            }}>
              <Typography sx={{ fontFamily: T.mono, fontSize: "1.0rem", color: T.cyan }}>
                {sourceName}
              </Typography>
              {crumbs.map((c) => (
                <React.Fragment key={c}>
                  <ChevronRight size={13} color={T.textMute as string} />
                  <Typography sx={{ fontFamily: T.mono, fontSize: "1.0rem", color: T.textDim, textTransform: "capitalize" }}>
                    {c}
                  </Typography>
                </React.Fragment>
              ))}
            </Box>
          </motion.div>
        )}
      </AnimatePresence>

      {/* ── AI status (top-right) ── */}
      <Box sx={{ position: "absolute", top: toolbar ? 62 : 12, right: 14, zIndex: 10 }}>
        <motion.div animate={{ opacity: statusCount >= STATUS_STEPS.length ? 0.45 : 1 }} transition={{ delay: 2 }}>
          <Box sx={{
            px: 1.5, py: 1, borderRadius: "10px",
            background: "rgba(7,11,20,0.7)", backdropFilter: "blur(10px)",
            border: `1px solid ${T.border}`,
            display: "flex", flexDirection: "column", gap: 0.6,
          }}>
            {STATUS_STEPS.map((step, i) => (
              <motion.div
                key={step}
                initial={{ opacity: 0, x: 10 }}
                animate={{ opacity: i < statusCount ? 1 : 0.22, x: 0 }}
                transition={{ duration: 0.35 }}
                style={{ display: "flex", alignItems: "center", gap: 8 }}
              >
                <svg width={10} height={10} viewBox="0 0 14 14" fill="none">
                  {i < statusCount && (
                    <motion.path
                      d="M2.5 7.5 L5.5 10.5 L11.5 3.5"
                      stroke={T.success} strokeWidth={2.4} strokeLinecap="round" strokeLinejoin="round"
                      initial={{ pathLength: 0 }} animate={{ pathLength: 1 }}
                      transition={{ duration: 0.3 }}
                    />
                  )}
                </svg>
                <Typography sx={{ fontFamily: T.mono, fontSize: "0.92rem", color: i < statusCount ? T.textDim : T.textMute }}>
                  {step}
                </Typography>
              </motion.div>
            ))}
          </Box>
        </motion.div>
      </Box>

      {/* ── tracing intro text ── */}
      <AnimatePresence>
        {tracing && (
          <motion.div
            initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
            style={{
              position: "absolute", bottom: 24, left: 0, right: 0, zIndex: 10,
              display: "flex", justifyContent: "center", pointerEvents: "none",
            }}
          >
            <Typography sx={{ fontFamily: T.mono, fontSize: "1.13rem", color: T.cyan, letterSpacing: "0.04em" }}>
              Tracing enterprise dependencies
              <motion.span animate={{ opacity: [1, 0.2] }} transition={{ repeat: Infinity, duration: 0.7, repeatType: "reverse" }}>
                …
              </motion.span>
            </Typography>
          </motion.div>
        )}
      </AnimatePresence>

      {/* ── AI commentary card ── */}
      <AnimatePresence>
        {selected && (
          <motion.div
            key={selected.title}
            initial={{ opacity: 0, y: 14, scale: 0.96 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 8 }}
            transition={{ type: "spring", stiffness: 320, damping: 26 }}
            style={{ position: "absolute", bottom: 18, left: 18, zIndex: 10, width: 236 }}
          >
            <Box sx={{
              ...T.glass, borderRadius: "14px", p: 2,
              borderColor: `${selected.hue}44`,
              boxShadow: `0 0 26px ${selected.hue}22, 0 8px 30px rgba(2,6,17,0.6)`,
            }}>
              <Box sx={{ display: "flex", alignItems: "center", gap: 1, mb: 1 }}>
                <Box sx={{ width: 8, height: 8, borderRadius: "50%", bgcolor: selected.hue, boxShadow: `0 0 10px ${selected.hue}` }} />
                <Typography sx={{
                  fontSize: "1.22rem", fontWeight: 700, color: T.text,
                  overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
                }}>
                  {selected.title}
                </Typography>
              </Box>
              {selected.lines.map(([k, v]) => (
                <Box key={k} sx={{ display: "flex", justifyContent: "space-between", gap: 1, mb: 0.4 }}>
                  <Typography sx={{ fontSize: "0.97rem", color: T.textMute, flexShrink: 0 }}>{k}</Typography>
                  <Typography sx={{
                    fontSize: "0.97rem", fontWeight: 600, textAlign: "right",
                    color: v === "critical" ? T.danger : T.textDim,
                    textTransform: k === "Examples" ? "none" : "capitalize",
                    overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
                  }}>
                    {v}
                  </Typography>
                </Box>
              ))}
            </Box>
          </motion.div>
        )}
      </AnimatePresence>

      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        onNodeClick={onNodeClick}
        onPaneClick={onPaneClick}
        onNodeMouseEnter={onNodeMouseEnter}
        onNodeMouseLeave={onNodeMouseLeave}
        nodesDraggable={false}
        nodesConnectable={false}
        fitView
        fitViewOptions={{ padding: 0.25 }}
        minZoom={0.15}
        proOptions={{ hideAttribution: true }}
        style={{ background: "transparent" }}
      >
        <Background variant={BackgroundVariant.Dots} gap={24} size={1} color="rgba(148,163,184,0.12)" />
        <MiniMap
          position="bottom-right"
          pannable zoomable
          nodeColor={(n) => (n.data as StoryNodeData)?.hue ?? "#64748B"}
          maskColor="rgba(7,11,20,0.78)"
          style={{ width: 170, height: 110 }}
        />
      </ReactFlow>
    </Box>
  );
};

const EnterpriseFlow: React.FC<EnterpriseFlowProps> = (props) => (
  <ReactFlowProvider>
    <InnerFlow {...props} />
  </ReactFlowProvider>
);

export default EnterpriseFlow;
