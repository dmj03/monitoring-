// Single place every HTTP call goes through. Components never call fetch().

const BASE = (import.meta.env?.VITE_API_BASE_URL as string) ?? "/api";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly detail: string,
    readonly fieldErrors?: { field: string; message: string }[],
  ) {
    super(detail);
    this.name = "ApiError";
  }
}

export class NetworkError extends Error {
  constructor() {
    super("Unable to connect to the monitoring API.");
    this.name = "NetworkError";
  }
}

function parseDetail(status: number, body: unknown): ApiError {
  // FastAPI sends {detail: "..."} for HTTPException and
  // {detail: [{loc, msg, ...}]} for 422 validation failures.
  const d = (body as { detail?: unknown })?.detail;
  if (Array.isArray(d)) {
    const fieldErrors = d.map((e: { loc?: unknown[]; msg?: string }) => ({
      field: String((e.loc ?? []).slice(-1)[0] ?? ""),
      message: e.msg ?? "invalid value",
    }));
    return new ApiError(status, "Please check the highlighted fields.", fieldErrors);
  }
  if (typeof d === "string") return new ApiError(status, d);
  return new ApiError(status, `Request failed with status ${status}.`);
}

export async function request<T>(
  path: string,
  init?: RequestInit & { query?: Record<string, string | number | undefined> },
): Promise<T> {
  const url = new URL(`${BASE}${path}`, window.location.origin);
  for (const [k, v] of Object.entries(init?.query ?? {})) {
    if (v !== undefined && v !== "") url.searchParams.set(k, String(v));
  }

  let res: Response;
  try {
    res = await fetch(url.toString().replace(window.location.origin, ""), {
      ...init,
      headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    });
  } catch {
    throw new NetworkError();
  }

  if (res.status === 204) return undefined as T;

  let body: unknown = null;
  try {
    body = await res.json();
  } catch {
    body = null;
  }

  if (!res.ok) throw parseDetail(res.status, body);
  return body as T;
}
