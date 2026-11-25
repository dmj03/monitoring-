// Mirrors backend/api/schemas.py exactly. Every field was read from the
// generated OpenAPI schema, not guessed. Nothing is added.

export interface Alert {
  alert_id: string;
  rule_id: string;
  customer_ref: string;
  programme_ref: string;
  created_at: string | null;
  shared_match_key: string | null;
  cluster_ref: string | null;
}

// programme_ref is derived from the customer by the server; if sent it must
// match. cluster_ref may only be null: membership comes from the cluster engine.
export interface AlertCreate {
  alert_id: string;
  rule_id: string;
  customer_ref: string;
  programme_ref?: string;
  created_at: string;
  shared_match_key?: string | null;
  cluster_ref?: null;
}

// GET /alerts/form-options
export interface AlertFormOptions {
  rules: { rule_id: string; playbook_id: string; category: string }[];
  customers: { customer_ref: string; programme_ref: string | null; name: string | null }[];
  programmes: { programme_ref: string; name: string | null }[];
  next_alert_id: string;
  alert_ids: string[];
}

export interface AlertList {
  alerts: Alert[];
  total: number;
  limit: number;
  offset: number;
}

export interface Evidence {
  key: string;
  status: string; // backend sends the literal "FOUND" or "MISSING"
  value: unknown;
  tool: string;
  field: string;
}

export interface ToolCall {
  tool: string;
  args: Record<string, unknown>;
  // NOTE: the backend's ToolCallOut carries no result field. The UI therefore
  // shows which calls were made and with which arguments, and links each
  // evidence row to the call that produced it. It does not invent a "Result".
}

export interface Investigation {
  alert_id: string;
  rule_id: string;
  category: string;
  playbook_id: string;
  cluster_ref: string | null;
  evidence: Evidence[];
  missing_evidence: string[];
  tool_calls: ToolCall[];
  investigation_focus: string;
  checklist: string[];
  escalation_guidance: string;
  ai_boundaries: string;
  allowed_outcomes: string[];
}

// POST /investigate/summary. No outcome, decision or confidence field exists:
// the model explains evidence and the analyst decides.
export interface AIStatement {
  text: string;
  evidence_keys: string[];
}

export interface AISummary {
  status: "generated" | "unavailable" | "error";
  label: "AI-generated";
  generated_by: "llm" | null;
  model: string | null;
  summary: string | null; // the narrative: summary_sentences joined
  summary_evidence_keys: string[];
  summary_sentences?: AIStatement[];
  key_findings: AIStatement[];
  missing_information: AIStatement[]; // the narrative's sentences about MISSING items
  analyst_attention: AIStatement[];
  evidence_basis: Evidence[];
  reason: string | null;
  cached?: boolean;
  generated_at?: string | null;
}

export interface InvestigationSummary {
  alert_id: string;
  rule_id: string;
  playbook_id: string;
  ai_summary: AISummary;
}

export interface Cluster {
  cluster_ref: string;
  cluster_type: string;
  label: string;
  cluster_category: string;
  alert_ids: string[];
  customer_refs: string[];
  programme_refs: string[];
  rule_ids: string[];
  time_span_hours: number;
  thresholds: Record<string, unknown>;
}

export interface ClusterList {
  clusters: Cluster[];
  membership: Record<string, string>;
  alerts_considered: number;
  alerts_clustered: number;
  note: string;
}

export interface Health {
  status: string;
  kb_version: string;
  playbooks: number;
  cluster_types: number;
  /** POST /alerts accepts new alerts. Off unless UC3_ALERT_CREATION is switched on. */
  alert_creation?: boolean;
}

// GET /routing and PUT /alerts/{alert_id}/routing. The analyst's decision,
// stored in data/routing.json. A record has a destination or an outcome.
export interface RoutingOption {
  id: string;
  label: string;
  description: string;
}

export interface RoutingRecord {
  alert_id: string;
  destination: string | null;
  outcome: string | null;
  routed_at: string;
  // "cluster" when the decision in force came from routing the alert's cluster.
  source?: "alert" | "cluster";
  cluster_type?: string | null;
  cluster_label?: string | null;
}

// A decision made on a cluster, for its exact member list at the time.
export interface ClusterRoutingRecord {
  key: string;
  cluster_type: string;
  label: string;
  alert_ids: string[];
  destination: string | null;
  outcome: string | null;
  routed_at: string;
}

export interface RoutingList {
  destinations: RoutingOption[];
  outcomes: RoutingOption[];
  routing: Record<string, RoutingRecord>;
  clusters?: ClusterRoutingRecord[];
}

// POST /clusters/summary. No routing, outcome, decision or confidence field.
export interface ClusterAIStatement {
  text: string;
  fields: string[];
}

export interface ClusterSummary {
  cluster_type: string;
  label: string;
  alert_ids: string[];
  ai_summary: {
    status: "generated" | "unavailable" | "error";
    label: "AI-generated";
    generated_by: "llm" | null;
    model: string | null;
    summary: string | null;
    summary_fields: string[];
    criteria_met: ClusterAIStatement[];
    characteristics: ClusterAIStatement[];
    reason: string | null;
  cached?: boolean;
  generated_at?: string | null;
  };
}
