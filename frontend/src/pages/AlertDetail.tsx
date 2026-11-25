import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { getAlert } from "../api/alerts";
import { investigate, summarize } from "../api/investigation";
import { AISummaryCard, type AiPhase } from "../components/AISummaryCard";
import { Empty, ErrorBox, Loading, SectionHead, errorText } from "../components/ui";
import { clusterOf, isPack, routingState, useClusters, useRouting } from "../lib/hooks";
import { RoutingPanel } from "../components/RoutingPanel";
import { setRouting } from "../api/routing";
import type { RoutingList } from "../api/types";
import { LIST_INLINE_MAX, fmtDetected, fmtValue, human, toolLabel } from "../lib/format";
import type { Alert, Evidence, Investigation, InvestigationSummary } from "../api/types";

type InvPhase = "running" | "done" | "failed";

function EvidenceItem({ e }: { e: Evidence }) {
  const missing = e.status === "MISSING";
  const list = Array.isArray(e.value) && e.value.length > LIST_INLINE_MAX ? (e.value as unknown[]) : null;
  return (
    <details id={`ev-${e.key}`} className={`ev-item${missing ? " ev-missing" : ""}`}>
      <summary>
        <span className={`ev-mark ${missing ? "m" : "f"}`} aria-hidden="true">
          {missing ? "◌" : "✓"}
        </span>
        <span className="ev-main">
          <span className="ev-label">{human(e.key)}</span>
          <span className={missing ? "ev-val-miss" : "ev-val"}>
            {missing ? "Not available in the collected evidence" : fmtValue(e.value)}
          </span>
        </span>
        <span className={missing ? "st st-miss" : "st st-ok"}>{e.status}</span>
      </summary>
      <div className="ev-prov">
        <span>
          Source <b>{toolLabel(e.tool)}</b>
        </span>
        <span className="mono">tool {e.tool}</span>
        <span className="mono">field {e.field}</span>
        <span className="mono">key {e.key}</span>
      </div>
      {list ? <div className="ev-refs mono">{list.map(String).join(", ")}</div> : null}
    </details>
  );
}

function Step({ state, label, detail }: { state: "todo" | "run" | "ok" | "bad"; label: string; detail?: string }) {
  const mark = { todo: "○", run: "", ok: "✓", bad: "!" }[state];
  return (
    <li className={`step s-${state}`}>
      <span className="step-mark" aria-hidden="true">
        {state === "run" ? <span className="spin" /> : mark}
      </span>
      <span className="step-label">{label}</span>
      {detail ? <span className="step-detail">{detail}</span> : null}
    </li>
  );
}

export default function AlertDetail() {
  const { alertId = "" } = useParams();
  const nav = useNavigate();
  const [alert, setAlert] = useState<Alert | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);

  const [invPhase, setInvPhase] = useState<InvPhase>("running");
  const [inv, setInv] = useState<Investigation | null>(null);
  const [invError, setInvError] = useState<unknown>(null);

  const [aiPhase, setAiPhase] = useState<AiPhase>("waiting");
  const [ai, setAi] = useState<InvestigationSummary | null>(null);
  const [aiError, setAiError] = useState<unknown>(null);
  const run = useRef(0); // guards against a stale response from an earlier run

  const clusters = useClusters(alertId);
  const cluster = clusterOf(clusters, alertId);

  // Routing is loaded once and updated locally after the analyst records a
  // decision. It never triggers a re-run of the investigation or the AI.
  const fetched = useRouting(alertId);
  const [routingData, setRoutingData] = useState<RoutingList | null>(null);
  useEffect(() => setRoutingData(fetched.data), [fetched.data]);
  const routing = { data: routingData, failed: fetched.failed };
  const routed = routingState(routingData, alertId);

  // The AI summary is its own request. It starts after the investigation
  // succeeds, and its failure never affects the investigation shown below.
  const runAi = useCallback(async (a: Alert, token: number, regenerate = false) => {
    setAiPhase("running");
    setAiError(null);
    try {
      const r = await summarize(a, regenerate);
      if (run.current !== token) return;
      setAi(r);
      setAiPhase(r?.ai_summary ? "done" : "failed");
    } catch (e) {
      if (run.current !== token) return;
      setAiError(e);
      setAiPhase("failed");
    }
  }, []);

  const runInvestigation = useCallback(
    async (a: Alert) => {
      const token = ++run.current;
      setInvPhase("running");
      setInvError(null);
      setAi(null);
      setAiPhase("waiting");
      try {
        const p = await investigate(a);
        if (run.current !== token) return;
        if (!isPack(p)) throw new Error("unexpected response");
        setInv(p);
        setInvPhase("done");
        void runAi(a, token);
      } catch (e) {
        if (run.current !== token) return;
        setInv(null);
        setInvError(e);
        setInvPhase("failed");
      }
    },
    [runAi],
  );

  useEffect(() => {
    run.current++;
    setLoading(true);
    setError(null);
    setInv(null);
    setAi(null);
    getAlert(alertId)
      .then((a) => {
        setAlert(a);
        void runInvestigation(a);
      })
      .catch((e) => {
        setError(e);
        setAlert(null);
      })
      .finally(() => setLoading(false));
  }, [alertId, runInvestigation]);

  if (loading) return <Loading what="Loading alert…" />;
  if (error)
    return (
      <div className="stack">
        <ErrorBox error={error} />
        <button className="btn" onClick={() => nav("/alerts")}>
          ‹ Back to alerts
        </button>
      </div>
    );
  if (!alert) return <Empty title="Alert not found." />;

  const ev = inv?.evidence ?? [];
  const found = ev.filter((e) => e.status !== "MISSING");
  const missing = ev.filter((e) => e.status === "MISSING");
  const groups = new Map<string, Evidence[]>();
  for (const e of ev) groups.set(e.tool, [...(groups.get(e.tool) ?? []), e]);

  const aiStep =
    aiPhase === "running"
      ? "run"
      : aiPhase === "done" && ai?.ai_summary.status === "generated"
        ? "ok"
        : aiPhase === "waiting"
          ? "todo"
          : "bad";

  return (
    <div className="stack detail">
      <Link to="/alerts" className="back">
        ← Alerts
      </Link>

      {/* A. Alert header */}
      <section className="card head-card">
        <div className="head-top">
          <div className="head-id">
            <div className="chips">
              <span className="mono strong">{alert.alert_id}</span>
              {cluster ? (
                <a href="#sec-related" className="pill pill-cl">
                  <span className="sw sw-cl" />
                  Clustered · {cluster.label} ({cluster.alert_ids.indexOf(alert.alert_id) + 1} of{" "}
                  {cluster.alert_ids.length})
                </a>
              ) : (
                <span className="pill">
                  <span className="sw sw-sa" />
                  Standalone alert
                </span>
              )}
            </div>
            <h1 className="head-title">{inv ? human(inv.category) : alert.rule_id}</h1>
          </div>
          <button className="btn" disabled={invPhase === "running"} onClick={() => void runInvestigation(alert)}>
            {invPhase === "running" ? "Investigating…" : "Re-run investigation"}
          </button>
        </div>
        <dl className="facts">
          <div>
            <dt>Rule</dt>
            <dd className="mono">{alert.rule_id}</dd>
          </div>
          <div>
            <dt>Customer</dt>
            <dd className="mono">{alert.customer_ref}</dd>
          </div>
          <div>
            <dt>Programme</dt>
            <dd className="mono">{alert.programme_ref}</dd>
          </div>
          <div>
            <dt>Detected</dt>
            <dd>{fmtDetected(alert.created_at)}</dd>
          </div>
          {alert.shared_match_key ? (
            <div>
              <dt>Shared match key</dt>
              <dd className="mono">{alert.shared_match_key}</dd>
            </div>
          ) : null}
          <div>
            <dt>Evidence</dt>
            <dd>
              {inv
                ? missing.length
                  ? `${found.length} found · ${missing.length} missing`
                  : `All ${ev.length} items found`
                : invPhase === "failed"
                  ? "Not available"
                  : "Collecting…"}
            </dd>
          </div>
          <div>
            <dt>Routing</dt>
            <dd>
              {!routingData ? (
                routing.failed ? "Not available" : "…"
              ) : routed.kind === "destination" ? (
                <a href="#sec-review" className="pill pill-route">
                  {routed.via ? "Routed via cluster" : "Routed"} · {routed.label}
                </a>
              ) : routed.kind === "outcome" ? (
                <a href="#sec-review" className="pill">
                  Closed · {routed.label}
                </a>
              ) : (
                <span className="dim">Not routed</span>
              )}
            </dd>
          </div>
        </dl>
        <ol className="steps" aria-label="Investigation progress">
          <Step state="ok" label="Alert loaded" />
          <Step
            state={invPhase === "running" ? "run" : invPhase === "done" ? "ok" : "bad"}
            label={
              invPhase === "running"
                ? "Running deterministic investigation"
                : invPhase === "done"
                  ? `Playbook: ${human(inv!.playbook_id)}`
                  : "Investigation failed"
            }
            detail={
              invPhase === "done"
                ? `${ev.length} evidence items from ${inv!.tool_calls.length} tool calls · ${found.length} found, ${missing.length} missing`
                : invPhase === "failed"
                  ? errorText(invError).title
                  : "routing the rule to its playbook and running the evidence tools"
            }
          />
          <Step
            state={aiStep}
            label="AI analyst summary"
            detail={
              aiStep === "run"
                ? "generating"
                : aiStep === "ok"
                  ? "generated"
                  : aiStep === "bad"
                    ? "unavailable"
                    : "waiting for evidence"
            }
          />
        </ol>
      </section>

      {invPhase === "failed" ? <ErrorBox error={invError} /> : null}

      {/* B. AI Analyst Summary */}
      {invPhase !== "failed" ? (
        <AISummaryCard
          phase={aiPhase}
          result={ai}
          error={aiError}
          onGenerate={() => {
            const token = ++run.current;
            void runAi(alert, token, true);
          }}
        />
      ) : null}

      {inv ? (
        <>
          {/* C + D + E. Investigation context, evidence, missing information */}
          <section id="sec-evidence" className="card sec sec-ev">
            <SectionHead
              n={2}
              tone="ev"
              title="Investigation & evidence"
              right={
                <span className="chips">
                  <span className="pill pill-ok">{found.length} FOUND</span>
                  <span className={`pill ${missing.length ? "pill-miss" : ""}`}>{missing.length} MISSING</span>
                </span>
              }
            />
            <div className="context">
              <div className="ctx-k">Why this alert was investigated</div>
              <p>
                Rule <span className="mono">{inv.rule_id}</span> is routed by the knowledge base to the{" "}
                <strong>{human(inv.playbook_id)}</strong> playbook. Its investigation focus:
              </p>
              <blockquote>{inv.investigation_focus}</blockquote>
            </div>

            {missing.length ? (
              <div className="missing-box">
                <div className="mb-head">
                  <strong>Missing information</strong>
                  <span>
                    MISSING means the value was not available in the collected evidence. It is a gap, not a
                    negative or failed result.
                  </span>
                </div>
                <ul>
                  {missing.map((e) => (
                    <li key={e.key}>
                      <a href={`#ev-${e.key}`}>⚠ {human(e.key)}</a>
                      <span className="dim"> · from {toolLabel(e.tool)}</span>
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}

            <div className="ev-groups">
              {[...groups.entries()].map(([tool, items]) => (
                <div key={tool} className="ev-group">
                  <div className="ev-group-head">
                    <span>{toolLabel(tool)}</span>
                    <span className="dim mono">
                      {items.filter((i) => i.status !== "MISSING").length}/{items.length}
                    </span>
                  </div>
                  {items.map((e) => (
                    <EvidenceItem key={e.key} e={e} />
                  ))}
                </div>
              ))}
            </div>
            <div className="fine">
              Values are exactly as returned by the evidence tools. Open an item to see where it came from.
            </div>

            <details className="disclose">
              <summary>Playbook guidance · checklist, escalation guidance and AI boundaries</summary>
              <div className="guide">
                <div>
                  <h3>Investigation checklist</h3>
                  <ol>
                    {inv.checklist.map((c) => (
                      <li key={c}>{c}</li>
                    ))}
                  </ol>
                </div>
                <div>
                  <h3>Escalation guidance</h3>
                  <p>{inv.escalation_guidance}</p>
                  <h3>AI boundaries for this playbook</h3>
                  <p>{inv.ai_boundaries}</p>
                </div>
              </div>
            </details>
          </section>

          {/* G. Investigation outcome: the analyst's routing decision */}
          <section id="sec-review" className="card sec sec-sys">
            <SectionHead
              n={3}
              tone="sys"
              title="Investigation outcome"
              right={<span className="dim">The analyst decides · the AI does not route alerts</span>}
            />
            <RoutingPanel
              id={alert.alert_id}
              noun="alert"
              current={routed}
              routing={routing.data}
              failed={routing.failed}
              allowedOutcomes={inv.allowed_outcomes}
              blockedReason={`the ${human(inv.playbook_id)} playbook does not permit it`}
              note={
                routed.via
                  ? `This alert is routed through its cluster (${routed.via}). Routing it here applies to this alert only and takes precedence over the cluster routing.`
                  : undefined
              }
              submit={async (body) => {
                const rec = await setRouting(alert.alert_id, body);
                setRoutingData((d) => (d ? { ...d, routing: { ...d.routing, [rec.alert_id]: rec } } : d));
              }}
            />
            <div className="permits">
              <span className="routed-k">This playbook permits</span>
              <span className="chips">
                {inv.allowed_outcomes.map((o) => (
                  <span key={o} className="chip">
                    {human(o)}
                  </span>
                ))}
              </span>
              <span className="fine">For reference. The AI does not suggest an outcome or a destination.</span>
            </div>
          </section>

          {/* Related pattern */}
          <section id="sec-related" className="card sec">
            <SectionHead
              n={4}
              tone="cl"
              title="Related pattern"
              right={<span className="dim mono">Recomputed on each request · not stored</span>}
            />
            {cluster ? (
              <div className="related">
                <div>
                  <div className="rel-title">{cluster.label}</div>
                  <div className="dim">
                    {cluster.alert_ids.length} alerts · {cluster.customer_refs.length} customers ·{" "}
                    {cluster.programme_refs.join(", ")} · span {cluster.time_span_hours}h
                  </div>
                </div>
                <div className="chips">
                  {cluster.alert_ids.map((id) => (
                    <button
                      key={id}
                      className={`chip-btn${id === alert.alert_id ? " on" : ""}`}
                      onClick={() => id !== alert.alert_id && nav(`/alerts/${id}`)}
                    >
                      {id}
                    </button>
                  ))}
                </div>
                <Link to="/clusters" state={{ alertId: alert.alert_id }} className="btn btn-sm btn-cl">
                  Open cluster review →
                </Link>
              </div>
            ) : (
              <p className="dim">
                {clusters
                  ? "This alert isn't part of a detected pattern at the current thresholds. It is investigated on its own."
                  : "Cluster context is not available right now."}
              </p>
            )}
          </section>

          {/* F. Provenance */}
          <details id="sec-tech" className="card disclose tech">
            <summary>
              How this evidence was collected{" "}
              <span className="dim">· tool calls, arguments and technical identifiers</span>
            </summary>
            <div className="tech-body">
              <h3>Tool calls · {inv.tool_calls.length} (deduplicated)</h3>
              <table className="mini">
                <thead>
                  <tr>
                    <th>Tool</th>
                    <th>Arguments</th>
                    <th>Evidence it supplied</th>
                  </tr>
                </thead>
                <tbody>
                  {inv.tool_calls.map((c, i) => (
                    <tr key={`${c.tool}-${i}`}>
                      <td className="mono">{c.tool}</td>
                      <td className="mono">
                        {Object.entries(c.args)
                          .map(([k, v]) => `${k}=${String(v)}`)
                          .join("  ")}
                      </td>
                      <td>
                        {ev
                          .filter((e) => e.tool === c.tool)
                          .map((e) => human(e.key))
                          .join(", ")}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <div className="fine">
                A tool call records the tool and its resolved arguments. It does not carry a result: the values are
                the evidence items above. Window and lookback arguments are passed through but do not filter yet
                (KNOWN_LIMITATIONS L1, L2), so no period is shown next to any value.
              </div>
              <h3>Identifiers</h3>
              <dl className="kv">
                <dt>alert_id</dt>
                <dd>{inv.alert_id}</dd>
                <dt>rule_id</dt>
                <dd>{inv.rule_id}</dd>
                <dt>category</dt>
                <dd>{inv.category}</dd>
                <dt>playbook_id</dt>
                <dd>{inv.playbook_id}</dd>
                <dt>shared_match_key</dt>
                <dd>{alert.shared_match_key ?? "null"}</dd>
                <dt>stored cluster_ref</dt>
                <dd>{String(alert.cluster_ref ?? "null")} (the pattern above comes from GET /clusters)</dd>
                <dt>detected cluster</dt>
                <dd>
                  {cluster ? `${cluster.cluster_ref} · ${cluster.cluster_type} (run reference, not durable)` : "none"}
                </dd>
              </dl>
            </div>
          </details>
        </>
      ) : invPhase === "running" ? (
        <div className="card sec">
          <Loading what="Running the deterministic investigation: routing the rule to its playbook, running the evidence tools and building the evidence pack…" />
        </div>
      ) : null}
    </div>
  );
}
