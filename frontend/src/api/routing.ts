import { request } from "./client";
import type { ClusterRoutingRecord, ClusterSummary, RoutingList, RoutingRecord } from "./types";

// Routing options plus every recorded decision. Alerts absent from `routing`
// have not been routed.
export const getRouting = () => request<RoutingList>("/routing");

// Exactly one of destination or outcome. Writes data/routing.json only.
export const setRouting = (alertId: string, body: { destination: string } | { outcome: string }) =>
  request<RoutingRecord>(`/alerts/${encodeURIComponent(alertId)}/routing`, {
    method: "PUT",
    body: JSON.stringify(body),
  });

// Route a currently detected cluster, identified by its exact member list.
export const setClusterRouting = (
  alertIds: string[],
  body: { destination: string } | { outcome: string },
) =>
  request<ClusterRoutingRecord>("/clusters/routing", {
    method: "PUT",
    body: JSON.stringify({ alert_ids: alertIds, ...body }),
  });

// AI explanation of why the deterministic engine grouped these alerts.
export const summarizeCluster = (alertIds: string[], regenerate = false) =>
  request<ClusterSummary>("/clusters/summary", {
    method: "POST",
    body: JSON.stringify({ alert_ids: alertIds }),
    query: regenerate ? { regenerate: "true" } : undefined,
  });

/** The key a cluster decision is stored under: type plus sorted member list. */
export const clusterKey = (clusterType: string, alertIds: string[]) =>
  `${clusterType}:${[...alertIds].sort().join(",")}`;
