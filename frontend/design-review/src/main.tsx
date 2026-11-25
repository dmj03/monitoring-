import React, { useEffect, useState } from "react";
import ReactDOM from "react-dom/client";
import "../proto.css";
import { SHOW } from "./data";
import { Frame, Prov, THEMES, type OptionDef, type ThemeId } from "./kit";
import { designSystem } from "./screens/designSystem";
import { overview } from "./screens/overview";
import { alertsScreens } from "./screens/alerts";
import { alertDetail } from "./screens/alertDetail";
import { investigationScreens } from "./screens/investigation";
import { aiScreens } from "./screens/aiSummary";
import { clusterScreens } from "./screens/clusters";
import { addAlertScreens } from "./screens/addAlert";

// Imported (not referenced by path) so a single-file build can embed them.
import imgOverview from "../current/1_overview.png";
import imgAlerts from "../current/2_alerts.png";
import imgInvestigation from "../current/6_investigation.png";
import imgMissing from "../current/7_missing_evidence.png";
import imgClusters from "../current/9_clusters.png";
import imgAdd from "../current/3_add_alert_modal.png";

const CURRENT = [
  [imgOverview, "Overview"], [imgAlerts, "Alerts"], [imgInvestigation, "Alert detail + investigation"],
  [imgMissing, "Investigation with MISSING evidence"], [imgClusters, "Clusters"], [imgAdd, "Add alert"],
];

const current: OptionDef[] = [{
  id: "screens", title: "Today's UI", tagline: "screenshots for reference", ownFrame: true,
  notes: {
    structure: "Screenshots of the current frontend taken during inspection (headless Edge, temporary dataset copy — some show test alerts ALRT-UI-*).",
    emphasis: "Baseline to compare against.", pros: ["—"], cons: ["—"], suits: "—", backend: ["—"], now: "—",
  },
  render: () => (
    <div className="grid2" style={{ gap: 16 }}>
      {CURRENT.map(([f, l]) => (
        <figure key={f} style={{ margin: 0 }}>
          <figcaption style={{ fontWeight: 600, margin: "0 0 6px" }}>{l}</figcaption>
          <img src={f} alt={l} style={{ width: "100%", border: "1px solid var(--h-line)", borderRadius: 8 }} />
        </figure>
      ))}
    </div>
  ),
}];

const SECTIONS: { id: string; label: string; options: OptionDef[] }[] = [
  { id: "current", label: "Current", options: current },
  { id: "system", label: "Design system", options: designSystem },
  { id: "overview", label: "Overview", options: overview },
  { id: "alerts", label: "Alerts", options: alertsScreens },
  { id: "detail", label: "Alert detail", options: alertDetail },
  { id: "investigation", label: "Investigation", options: investigationScreens },
  { id: "ai", label: "AI summary", options: aiScreens },
  { id: "clusters", label: "Clusters", options: clusterScreens },
  { id: "add", label: "Add alert", options: addAlertScreens },
];

const SAMPLES = [
  [SHOW.clustered, "ALRT-0001 · clean, in a pattern"],
  [SHOW.gaps, "ALRT-0073 · 2 missing items"],
  [SHOW.screening, "ALRT-0067 · sanctions possible match"],
] as const;

function parse(): [string, string] {
  const [s, o] = location.hash.replace(/^#\/?/, "").split("/");
  const sec = SECTIONS.find((x) => x.id === s) ?? SECTIONS[1];
  const opt = sec.options.find((x) => x.id === o) ?? sec.options[0];
  return [sec.id, opt.id];
}

function load<T extends string>(k: string, d: T): T {
  try { return (localStorage.getItem(k) as T) || d; } catch { return d; }
}
function save(k: string, v: string) {
  try { localStorage.setItem(k, v); } catch { /* per-viewer convenience only */ }
}

function App() {
  const [[secId, optId], setLoc] = useState(parse);
  const [theme, setTheme] = useState<ThemeId>(() => load("dr-theme", "enterprise"));
  const [sample, setSample] = useState<string>(() => load("dr-sample", SHOW.gaps));
  useEffect(() => {
    const on = () => setLoc(parse());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  const sec = SECTIONS.find((s) => s.id === secId)!;
  const opt = sec.options.find((o) => o.id === optId)!;
  const n = opt.notes;
  return (
    <>
      <div className="h-top">
        <span className="h-title">UC3 · Frontend design review<small>prototypes — not the product, static snapshot data</small></span>
        <nav className="h-sections">
          {SECTIONS.map((s) => <a key={s.id} href={`#/${s.id}`} className={s.id === secId ? "on" : ""}>{s.label}</a>)}
        </nav>
        <span className="h-theme">
          Direction
          {THEMES.map((t) => (
            <button key={t.id} className={t.id === theme ? "on" : ""} onClick={() => { setTheme(t.id); save("dr-theme", t.id); }}>{t.label}</button>
          ))}
        </span>
      </div>
      <div className="h-wrap">
        {secId === "system" && optId === "directions" ? (
          <div className="h-intro">
            <strong>How to use this.</strong> Pick a section above, then compare its options. The <strong>Direction</strong> switch
            re-skins every screen, so layout and visual style can be judged separately. Screens marked with a sample switch can be
            viewed for three real alerts. All data is a read-only snapshot of real API responses. Labels show where each block comes from:
            <div className="h-legend" data-theme="enterprise" style={{ background: "transparent" }}>
              <Prov kind="engine" /> <Prov kind="computed" /> <Prov kind="kb" /> <Prov kind="future" /> <Prov kind="ai" />
            </div>
          </div>
        ) : null}
        <div className="h-opts">
          {sec.options.map((o) => (
            <a key={o.id} href={`#/${sec.id}/${o.id}`} className={o.id === optId ? "on" : ""}>{o.title}<small>{o.tagline}</small></a>
          ))}
          {opt.usesAlert ? (
            <span className="h-opts" style={{ marginLeft: "auto", margin: 0, alignItems: "center" }}>
              <span style={{ fontSize: 12, color: "var(--h-ink-2)" }}>Sample alert</span>
              {SAMPLES.map(([id, l]) => (
                <a key={id} href={location.hash} onClick={(e) => { e.preventDefault(); setSample(id); save("dr-sample", id); }}
                  className={id === sample ? "on" : ""}>{l}</a>
              ))}
            </span>
          ) : null}
        </div>
        <details className="h-notes" open>
          <summary>{opt.title} — design notes</summary>
          <div className="grid">
            <div><h4>Visual structure</h4><p>{n.structure}</p></div>
            <div><h4>Emphasises</h4><p>{n.emphasis}</p></div>
            <div><h4>Suits</h4><p>{n.suits}</p></div>
            <div><h4>Advantages</h4><ul>{n.pros.map((x) => <li key={x}>{x}</li>)}</ul></div>
            <div><h4>Disadvantages</h4><ul>{n.cons.map((x) => <li key={x}>{x}</li>)}</ul></div>
            <div><h4>Possible with current API</h4><p>{n.now}</p></div>
            <div style={{ gridColumn: "1 / -1" }}><h4>Backend / API implications</h4><ul>{n.backend.map((x) => <li key={x}>{x}</li>)}</ul></div>
          </div>
        </details>
        {opt.ownFrame ? opt.render(theme, sample) : <Frame theme={theme}>{opt.render(theme, sample)}</Frame>}
      </div>
    </>
  );
}

ReactDOM.createRoot(document.getElementById("root")!).render(<React.StrictMode><App /></React.StrictMode>);
