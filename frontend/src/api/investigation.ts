import { request } from "./client";
import type { Alert, Investigation, InvestigationSummary } from "./types";

// POST /investigate takes the whole alert under an "alert" key, matching
// InvestigateRequest. It runs an investigation and persists nothing.
export const investigate = (alert: Alert) =>
  request<Investigation>("/investigate", {
    method: "POST",
    body: JSON.stringify({ alert }),
  });

// POST /investigate/summary runs the same investigation server-side and asks
// the model to explain the resulting evidence. AI failure is a 200 with
// ai_summary.status "unavailable" or "error", never an HTTP error.
// regenerate=true bypasses the server cache of accepted summaries and makes a
// fresh model call; without it a cached summary is reused with no model call.
export const summarize = (alert: Alert, regenerate = false) =>
  request<InvestigationSummary>("/investigate/summary", {
    method: "POST",
    body: JSON.stringify({ alert }),
    query: regenerate ? { regenerate: "true" } : undefined,
  });
