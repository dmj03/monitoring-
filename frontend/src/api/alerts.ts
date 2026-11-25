import { request } from "./client";
import type { Alert, AlertCreate, AlertFormOptions, AlertList } from "./types";

export interface AlertQuery {
  rule_id?: string;
  programme_ref?: string;
  customer_ref?: string;
  limit?: number;
  offset?: number;
}

export const listAlerts = (q: AlertQuery = {}) =>
  request<AlertList>("/alerts", { query: { ...q } });

export const getAlert = (alertId: string) =>
  request<Alert>(`/alerts/${encodeURIComponent(alertId)}`);

export const createAlert = (body: AlertCreate) =>
  request<Alert>("/alerts", { method: "POST", body: JSON.stringify(body) });

export const getAlertFormOptions = () => request<AlertFormOptions>("/alerts/form-options");
