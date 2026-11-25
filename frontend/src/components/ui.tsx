import { NavLink, useLocation } from "react-router-dom";
import { useEffect, useState } from "react";
import { getHealth } from "../api/health";
import { ApiError, NetworkError } from "../api/client";
import type { Health } from "../api/types";
import { getPreference, initTheme, setPreference, type ThemePref } from "../theme/theme";

export function Loading({ what }: { what: string }) {
  return (
    <div className="state" role="status">
      {what}
    </div>
  );
}

export function Empty({ title, hint }: { title: string; hint?: string }) {
  return (
    <div className="state">
      <strong>{title}</strong>
      {hint}
    </div>
  );
}

/** Turns any thrown error into analyst-readable text. Never shows a traceback. */
export function errorText(e: unknown): { title: string; body: string } {
  if (e instanceof NetworkError)
    return {
      title: "Unable to connect to the monitoring API.",
      body: "Check that the FastAPI server is running on the configured address.",
    };
  if (e instanceof ApiError) {
    if (e.status === 404) return { title: "Not found.", body: e.detail };
    if (e.status === 409)
      return { title: "Alert already exists", body: "An alert with this Alert ID already exists." };
    if (e.status === 422)
      return { title: "Please check the highlighted fields.", body: "" };
    if (e.status >= 500)
      return { title: "The monitoring API reported an error.", body: "Try again shortly." };
    return { title: "Request rejected.", body: e.detail };
  }
  return { title: "Something unexpected happened.", body: "" };
}

export function ErrorBox({ error }: { error: unknown }) {
  const { title, body } = errorText(error);
  return (
    <div className="err-box" role="alert">
      <strong>{title}</strong>
      {body}
    </div>
  );
}

export function Badge({ kind, children }: { kind: string; children: React.ReactNode }) {
  return <span className={`badge b-${kind}`}>{children}</span>;
}

/** Small numbered section heading used on the investigation page. */
export function SectionHead({
  n,
  tone,
  title,
  right,
}: {
  n?: number;
  tone: "sys" | "ev" | "ai" | "cl";
  title: string;
  right?: React.ReactNode;
}) {
  return (
    <div className="sec-head">
      <div className="sec-title">
        {n !== undefined ? <span className={`sec-n t-${tone}`}>{n}</span> : null}
        <h2>{title}</h2>
      </div>
      {right ? <div className="sec-right">{right}</div> : null}
    </div>
  );
}

function crumbFor(path: string): string {
  if (path.startsWith("/alerts/")) return `Alerts / ${decodeURIComponent(path.slice(8))}`;
  if (path.startsWith("/clusters")) return "Clusters";
  return "Alerts";
}

/** Light / Dark / System switch from the shared theme kit, at the bottom of the sidebar. */
function ThemeToggle() {
  const [pref, setPref] = useState<ThemePref>(getPreference);
  useEffect(() => initTheme(() => setPref(getPreference())), []);
  const choose = (p: ThemePref) => {
    setPreference(p);
    setPref(p);
  };
  return (
    <div className="theme-toggle" role="group" aria-label="Colour theme">
      {(
        [
          ["light", "Light"],
          ["dark", "Dark"],
          ["system", "System"],
        ] as const
      ).map(([p, label]) => (
        <button key={p} type="button" aria-pressed={pref === p} onClick={() => choose(p)}>
          {label}
        </button>
      ))}
    </div>
  );
}

export function Shell({ children }: { children: React.ReactNode }) {
  const [health, setHealth] = useState<Health | null>(null);
  const [down, setDown] = useState(false);
  const loc = useLocation();

  useEffect(() => {
    let alive = true;
    const poll = () =>
      getHealth()
        .then((h) => alive && (setHealth(h), setDown(false)))
        .catch(() => alive && setDown(true));
    poll();
    const t = setInterval(poll, 15000);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, []);

  const ok = !down && health?.status === "ok";
  return (
    <div className="shell">
      <aside className="side">
        <div className="brand">
          <span className="brand-tile" aria-hidden="true">
            UC3
          </span>
          <span className="brand-text">
            Alert Triage
            <small>UC3 · Monitoring POC</small>
          </span>
        </div>
        <nav className="nav">
          <div className="nav-label">Monitoring</div>
          {[
            ["/alerts", "Alerts"],
            ["/clusters", "Clusters"],
          ].map(([to, label]) => (
            <NavLink key={to} to={to} className={({ isActive }) => (isActive ? "on" : "")}>
              {label}
            </NavLink>
          ))}
        </nav>
        <div className="side-foot">
          <div className="side-k">Backend</div>
          <div className="side-v">
            <span
              className={`dot${health || down ? (ok ? " dot-ok" : " dot-bad") : ""}`}
              aria-hidden="true"
            />
            {health || down ? (ok ? "API connected" : "API unreachable") : "Connecting…"}
          </div>
          {health ? (
            <div className="side-m">
              {health.kb_version} · {health.playbooks} playbooks · {health.cluster_types} cluster types
            </div>
          ) : null}
          <div className="side-note">
            Investigation is read-only. The only writes are adding an alert and recording the
            analyst&apos;s routing decision. The analyst makes every decision.
          </div>
          <ThemeToggle />
        </div>
      </aside>
      <div className="main-col">
        <header className="topbar">
          <div className="crumb">{crumbFor(loc.pathname)}</div>
          <div className="legend" aria-label="Colour key">
            <span className="lg t-sys">System</span>
            <span className="lg t-ev">Evidence</span>
            <span className="lg t-ai">AI</span>
          </div>
        </header>
        <main className="main">{children}</main>
      </div>
    </div>
  );
}
