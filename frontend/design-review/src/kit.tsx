import { createContext, useContext, type ReactNode } from "react";
import { fmtValue, health, type Evidence } from "./data";

export type ThemeId = "enterprise" | "aiops" | "minimal" | "soc";
export const THEMES: { id: ThemeId; label: string }[] = [
  { id: "enterprise", label: "A Enterprise" },
  { id: "aiops", label: "B AI ops" },
  { id: "minimal", label: "C Minimal" },
  { id: "soc", label: "D Dark SOC" },
];
export const ThemeCtx = createContext<ThemeId>("enterprise");
export const useTheme = () => useContext(ThemeCtx);

/** Provenance label: makes the source of every block explicit. */
export function Prov({ kind }: { kind: "engine" | "kb" | "ai" | "future" | "computed" }) {
  const text = {
    engine: "Deterministic engine",
    kb: "From KB playbook",
    ai: "AI summary — planned, not built",
    future: "Future backend field",
    computed: "Computed in UI from existing API",
  }[kind];
  return <span className={`prov ${kind}`}>{text}</span>;
}

export const Badge = ({ k, children }: { k: string; children: ReactNode }) => <span className={`b ${k}`}>{children}</span>;

export function Card({ title, right, children, className = "" }: { title?: ReactNode; right?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`card ${className}`}>
      {title || right ? (
        <div className="hd">
          <h2 className="s">{title}</h2>
          <div className="row">{right}</div>
        </div>
      ) : null}
      <div className="bd">{children}</div>
    </section>
  );
}

/** One evidence status rendering. MISSING never reads as false / failed / 0. */
export function EvStatus({ e }: { e: Evidence }) {
  return e.status === "MISSING" ? <Badge k="warn">○ Missing</Badge> : <Badge k="ok">● Found</Badge>;
}
export function EvValue({ e }: { e: Evidence }) {
  return e.status === "MISSING" ? (
    <span className="ev-missing-dash" title="No value in the source data. This is a finding, not a failure.">
      — not in source data
    </span>
  ) : (
    <span className="mono">{fmtValue(e.value)}</span>
  );
}

export function Meter({ found, missing }: { found: number; missing: number }) {
  const t = found + missing || 1;
  return (
    <div className="meter" title={`${found} found, ${missing} missing`}>
      <i className="f" style={{ width: `${(found / t) * 100}%` }} />
      <i className="m" style={{ width: `${(missing / t) * 100}%` }} />
    </div>
  );
}

const NAV = [
  ["overview", "Overview", "◫"],
  ["alerts", "Alerts", "≡"],
  ["clusters", "Clusters", "⋰"],
] as const;

/** The mock product shell. Sidebar form varies by visual direction. */
export function AppShell({ active, children, nav = NAV as unknown as [string, string, string][] }: {
  active: string;
  children: ReactNode;
  nav?: [string, string, string][];
}) {
  const theme = useTheme();
  const status = (
    <span className="status">
      <span><span className="dot" />API connected</span>
      <span className="mono">KB {health.kb_version}</span>
    </span>
  );
  if (theme === "minimal") {
    return (
      <div className="app topnav">
        <div>
          <div className="tnav">
            <span className="brand">UC3 Alert Triage</span>
            {nav.map(([k, l]) => <a key={k} className={k === active ? "on" : ""}>{l}</a>)}
            <span className="right">{status}</span>
          </div>
          <div className="content">{children}</div>
        </div>
      </div>
    );
  }
  const rail = theme === "soc";
  return (
    <div className="app" style={rail ? ({ "--side-w": "64px" } as React.CSSProperties) : undefined}>
      <aside className={`side${rail ? " rail" : ""}`}>
        <div className="brand">{rail ? "UC3" : "UC3 Alert Triage"}</div>
        {nav.map(([k, l, i]) => (
          <a key={k} className={k === active ? "on" : ""}>
            <span className="ico">{i}</span>
            {l}
          </a>
        ))}
        {!rail ? <div className="foot">Monitoring alert triage · POC</div> : null}
      </aside>
      <div className="content">
        <div className="topline">
          <span className="crumbs" />
          {status}
        </div>
        {children}
      </div>
    </div>
  );
}

export function Frame({ theme, children }: { theme: ThemeId; children: ReactNode }) {
  return (
    <ThemeCtx.Provider value={theme}>
      <div className="frame" data-theme={theme} style={{ position: "relative" }}>
        {children}
      </div>
    </ThemeCtx.Provider>
  );
}

export interface OptionNotes {
  structure: string;
  emphasis: string;
  pros: string[];
  cons: string[];
  suits: string;
  backend: string[];
  now: string;
}

export interface OptionDef {
  id: string;
  title: string;
  tagline: string;
  notes: OptionNotes;
  /** Rendered inside a themed Frame by the harness unless ownFrame is set. */
  render: (theme: ThemeId, alertId: string) => ReactNode;
  ownFrame?: boolean;
  /** Show the sample-alert switcher for this option. */
  usesAlert?: boolean;
}
