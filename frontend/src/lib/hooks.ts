import { useEffect, useState } from "react";
import { investigate } from "../api/investigation";
import { listClusters } from "../api/clusters";
import { getRouting } from "../api/routing";
import type { Alert, Cluster, ClusterList, Investigation, RoutingList } from "../api/types";

export type InvState =
  | { st: "loading" }
  | { st: "ok"; pack: Investigation }
  | { st: "error"; error: unknown };

/** A response is only treated as an evidence pack if it has the pack's shape. */
export function isPack(x: unknown): x is Investigation {
  const p = x as Investigation | null;
  return !!p && Array.isArray(p.evidence) && Array.isArray(p.tool_calls) && typeof p.category === "string";
}

/**
 * Runs the existing read-only POST /investigate for each given alert, so a list
 * can show category and evidence state. Nothing is stored server-side; results
 * live only in this component's state.
 */
export function useInvestigations(alerts: Alert[]): Record<string, InvState> {
  const [inv, setInv] = useState<Record<string, InvState>>({});
  const key = alerts.map((a) => a.alert_id).join("|");

  useEffect(() => {
    let live = true;
    const todo = alerts.filter((a) => !inv[a.alert_id]);
    if (!todo.length) return;
    setInv((m) => ({ ...m, ...Object.fromEntries(todo.map((a) => [a.alert_id, { st: "loading" } as InvState])) }));
    for (const a of todo) {
      investigate(a)
        .then((p) => {
          if (!live) return;
          setInv((m) => ({
            ...m,
            [a.alert_id]: isPack(p) ? { st: "ok", pack: p } : { st: "error", error: new Error("unexpected response") },
          }));
        })
        .catch((error) => live && setInv((m) => ({ ...m, [a.alert_id]: { st: "error", error } })));
    }
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  return inv;
}

/** GET /clusters, computed over the whole dataset on each call. Failure = no cluster context. */
export function useClusters(refreshKey: unknown = 0): ClusterList | null {
  const [data, setData] = useState<ClusterList | null>(null);
  useEffect(() => {
    let live = true;
    listClusters()
      .then((d) => live && setData(Array.isArray(d?.clusters) ? d : null))
      .catch(() => live && setData(null));
    return () => {
      live = false;
    };
  }, [refreshKey]);
  return data;
}

/** The detected cluster an alert belongs to in this run, if any. */
export function clusterOf(data: ClusterList | null, alertId: string): Cluster | null {
  const ref = data?.membership?.[alertId];
  return ref ? data!.clusters.find((c) => c.cluster_ref === ref) ?? null : null;
}

/** GET /routing. null while loading or if it could not be read. */
export function useRouting(refreshKey: unknown = 0): { data: RoutingList | null; failed: boolean } {
  const [data, setData] = useState<RoutingList | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let live = true;
    getRouting()
      .then((d) => {
        if (!live) return;
        const ok = !!d && Array.isArray(d.destinations) && Array.isArray(d.outcomes) && typeof d.routing === "object";
        setData(ok ? d : null);
        setFailed(!ok);
      })
      .catch(() => live && (setData(null), setFailed(true)));
    return () => {
      live = false;
    };
  }, [refreshKey]);
  return { data, failed };
}

/** The readable state of an alert's routing decision. */
export function routingState(
  list: RoutingList | null,
  alertId: string,
): RoutingView {
  const rec = list?.routing?.[alertId] ?? null;
  const via = rec?.source === "cluster" ? (rec.cluster_label ?? "cluster") : null;
  return viewOf(list, rec, via);
}

export interface RoutingView {
  kind: "destination" | "outcome" | "none";
  label: string;
  record: { destination: string | null; outcome: string | null; routed_at: string } | null;
  /** Label of the cluster the decision came from, when it came from a cluster. */
  via: string | null;
}

export function viewOf(
  list: RoutingList | null,
  rec: { destination: string | null; outcome: string | null; routed_at: string } | null,
  via: string | null = null,
): RoutingView {
  if (rec?.destination)
    return { kind: "destination", label: list!.destinations.find((d) => d.id === rec.destination)?.label ?? rec.destination, record: rec, via };
  if (rec?.outcome)
    return { kind: "outcome", label: list!.outcomes.find((o) => o.id === rec.outcome)?.label ?? rec.outcome, record: rec, via };
  return { kind: "none", label: "Not routed", record: null, via: null };
}
