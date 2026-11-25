// Static snapshot of real API responses (see ../README.md). No live calls.
import raw from "../data/snapshot.json";
import type { Alert, Cluster, ClusterList, Evidence, Health, Investigation } from "../../src/api/types";

export type { Alert, Cluster, Evidence, Investigation };

interface KbEvidence { required: boolean; missing_behaviour: string }
interface KbPlaybook { description: string; purpose: string; evidence: Record<string, KbEvidence> }
interface KbCluster { purpose: string; interpretation_guidance: string; analyst_guidance: string[]; boundaries: string[] }
export interface LlmInput {
  alert: { alert_id: string; rule_id: string; category: string };
  investigation_focus: string;
  checklist: string[];
  escalation_guidance: string;
  global_boundaries: { model_may: string[]; model_must_not: string[]; screening_specific: string[]; outcome_contract: string };
  boundaries: string;
  evidence: Record<string, unknown>;
  missing_evidence: string[];
  permitted_outcomes: string[];
  instruction: string;
}

interface Snapshot {
  api: {
    health: Health;
    alerts: { alerts: Alert[]; total: number };
    clusters: ClusterList;
    investigations: Record<string, Investigation>;
  };
  engine_not_api: { llm_inputs: Record<string, LlmInput> };
  kb_not_api: {
    routing: Record<string, string>;
    investigation_outcomes: string[];
    playbooks: Record<string, KbPlaybook>;
    clusters: Record<string, KbCluster>;
  };
}

const S = raw as unknown as Snapshot;

export const health = S.api.health;
export const alerts = S.api.alerts.alerts;
export const clusterList = S.api.clusters;
export const clusters = S.api.clusters.clusters;
export const llmInput = (id: string) => S.engine_not_api.llm_inputs[id];
export const kb = S.kb_not_api;

export const alertById = (id: string) => alerts.find((a) => a.alert_id === id)!;
export const investigation = (id: string) => S.api.investigations[id];

/** Cluster membership comes from GET /clusters, NOT from alert.cluster_ref (null on every stored alert). */
export const clusterOf = (alertId: string): Cluster | null => {
  const ref = clusterList.membership[alertId];
  return ref ? clusters.find((c) => c.cluster_ref === ref) ?? null : null;
};

/** Gap count per alert. Needs one POST /investigate per alert: fine for a snapshot, see notes. */
export const gapCount = (id: string) => investigation(id).missing_evidence.length;

/** KB-only: whether a MISSING item bears on the outcome. FUTURE BACKEND FIELD. */
export const kbEvidence = (inv: Investigation, key: string) => kb.playbooks[inv.playbook_id]?.evidence[key];

// Showcase alerts, all real.
export const SHOW = {
  clustered: "ALRT-0001", // velocity, member of a repeated_velocity cluster, all evidence FOUND
  gaps: "ALRT-0073", // counterparty review, 2 MISSING items
  screening: "ALRT-0067", // sanctions/PEP possible match, all FOUND
} as const;

export const human = (s: string) => s.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());

export function fmtTime(iso: string | null) {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toISOString().replace("T", " ").slice(0, 16) + " UTC";
}

export function fmtValue(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "boolean") return v ? "Yes" : "No";
  if (Array.isArray(v)) return v.length <= 3 ? v.join(", ") : `${v.slice(0, 3).join(", ")} +${v.length - 3}`;
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

export const ruleIds = Object.keys(kb.routing);
export const programmes = [...new Set(alerts.map((a) => a.programme_ref))].sort();

export const byCategory = () => {
  const m = new Map<string, number>();
  for (const a of alerts) {
    const c = investigation(a.alert_id).category;
    m.set(c, (m.get(c) ?? 0) + 1);
  }
  return [...m.entries()].sort((a, b) => b[1] - a[1]);
};

export const recent = (n: number) =>
  [...alerts].sort((a, b) => String(b.created_at).localeCompare(String(a.created_at))).slice(0, n);
