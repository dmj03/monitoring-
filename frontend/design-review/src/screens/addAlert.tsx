import { alerts, human, kb, programmes, recent, ruleIds } from "../data";
import { AppShell, Badge, Prov, type OptionDef } from "../kit";

function RuleSelect() {
  return (
    <select className="inp" defaultValue="TM-VEL-014">
      {ruleIds.map((r) => <option key={r} value={r}>{r} — {human(kb.routing[r])}</option>)}
    </select>
  );
}

function Fields({ state }: { state: "clean" | "errors" }) {
  const bad = state === "errors";
  return (
    <>
      <div className="fld">
        <label>Alert ID</label>
        <input className={`inp mono${bad ? " bad" : ""}`} defaultValue={bad ? "ALRT-0001" : "ALRT-0091"} />
        {bad ? <span className="err">ALRT-0001 already exists. Alert IDs are unique and existing alerts are never overwritten. (409)</span> : <span className="hint">Must be unique.</span>}
      </div>
      <div className="fld">
        <label>Rule <Prov kind="future" /></label>
        <RuleSelect />
        <span className="hint">The 10 rules the KB routes. Shown with the playbook each selects.</span>
      </div>
      <div className="grid2">
        <div className="fld"><label>Customer</label><input className="inp mono" defaultValue="CUST-0101" /><span className="hint">Unknown customers are allowed — evidence will be MISSING.</span></div>
        <div className="fld"><label>Programme</label><select className="inp mono">{programmes.map((p) => <option key={p}>{p}</option>)}</select></div>
      </div>
      <div className="fld">
        <label>Created at (UTC)</label>
        <div className="row" style={{ flexWrap: "nowrap" }}>
          <input className={`inp mono${bad ? " bad" : ""}`} defaultValue={bad ? "yesterday" : "2026-10-03T14:30:00Z"} />
          <button className="btn sm">Now</button>
        </div>
        {bad ? <span className="err">created_at must be an ISO-8601 timestamp, e.g. 2026-10-03T14:30:00Z (422 — message from the API)</span> : <span className="hint">ISO-8601 with timezone. Used for pattern time windows.</span>}
      </div>
      <details className="tech"><summary>Optional correlation fields</summary>
        <div className="fld" style={{ marginTop: 6 }}><label>Shared match key</label><input className="inp mono" placeholder="e.g. CP-7781" /><span className="hint">Only meaningful for CP-LINK-071 / MC-RISK-041 (shared counterparty pattern).</span></div>
        <div className="fld"><label>Cluster reference</label><input className="inp mono" disabled placeholder="not recommended — see notes" /><span className="warnmsg">Stored but never used: clusters are computed. Candidate for removal from the form.</span></div>
      </details>
    </>
  );
}

function AddA({ state }: { state: "clean" | "errors" }) {
  return (
    <AppShell active="alerts">
      <div style={{ minHeight: 640 }}>
        <h1 className="t">Alerts</h1>
        <div className="scrim">
          <div className="modal">
            <div style={{ padding: "14px 18px", borderBottom: "1px solid var(--border)", fontWeight: 600 }}>Add alert</div>
            <div style={{ padding: 18 }}>
              {state === "errors" ? <div className="banner danger sm" style={{ marginBottom: 12 }}>2 fields need attention. Nothing was saved.</div> : null}
              <Fields state={state} />
            </div>
            <div className="row" style={{ padding: "12px 18px", borderTop: "1px solid var(--border)", justifyContent: "flex-end" }}>
              <span className="xs muted" style={{ marginRight: "auto" }}>Saved permanently to the dataset.</span>
              <button className="btn">Cancel</button><button className="btn p">Create alert</button>
            </div>
          </div>
        </div>
      </div>
    </AppShell>
  );
}

function AddB() {
  return (
    <AppShell active="alerts">
      <div className="stack">
        <div className="crumbs">Alerts / <b>New alert</b></div>
        <h1 className="t">New alert</h1>
        <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) 340px", gap: 18, alignItems: "start" }}>
          <div className="card"><div className="bd"><Fields state="clean" /><div className="row"><button className="btn p">Create and investigate</button><button className="btn">Create</button><button className="btn ghost">Cancel</button></div></div></div>
          <div className="card"><div className="bd stack">
            <div className="eyebrow">What will happen <Prov kind="future" /></div>
            <div className="sm">Rule <b className="mono">TM-VEL-014</b> routes to playbook <b>{human(kb.routing["TM-VEL-014"])}</b>.</div>
            <div className="sm">The investigation will request {Object.keys(kb.playbooks[kb.routing["TM-VEL-014"]].evidence).length} evidence items for CUST-0101.</div>
            <div className="sm">It will be considered for the <b>repeated velocity</b> pattern in programme P001 on the next pattern run.</div>
            <div className="xs muted">Needs KB routing / playbook evidence lists from the API (FUTURE: GET /rules or GET /playbooks).</div>
          </div></div>
        </div>
      </div>
    </AppShell>
  );
}

function AddC() {
  const list = recent(8);
  return (
    <AppShell active="alerts">
      <div style={{ position: "relative", minHeight: 640 }}>
        <div className="stack" style={{ marginRight: 430 }}>
          <h1 className="t">Alerts</h1>
          <div className="banner ok sm">ALRT-0091 created — pinned to the top until you leave this page.</div>
          <div className="card"><table className="tb"><thead><tr><th>Alert</th><th>Rule</th><th>Customer</th></tr></thead><tbody>
            <tr className="sel"><td className="id">ALRT-0091 <Badge k="ok">new</Badge></td><td className="mono">TM-VEL-014</td><td className="mono">CUST-0101</td></tr>
            {list.map((a) => <tr key={a.alert_id}><td className="id">{a.alert_id}</td><td className="mono">{a.rule_id}</td><td className="mono">{a.customer_ref}</td></tr>)}
          </tbody></table></div>
          <div className="xs muted">Today the new alert is appended to the end of dataset order (page 4 of 4 with {alerts.length + 1} alerts) — this design pins it instead.</div>
        </div>
        <div className="drawer">
          <h2 className="s" style={{ marginBottom: 12 }}>Add alert</h2>
          <Fields state="clean" />
          <div className="row"><button className="btn p">Create</button><button className="btn">Create another</button></div>
        </div>
      </div>
    </AppShell>
  );
}

const be = [
  "Rule dropdown: today the UI can only derive rule IDs from existing alerts (all 10 happen to be present). A FUTURE GET /rules (KB routing) would make it authoritative.",
  "Unknown rule_id: currently ACCEPTED by POST /alerts and then un-investigable (400). Pending your decision; a dropdown avoids it in the UI but not at the API.",
  "422 field messages: the API already returns them (e.g. created_at); today's modal shows only 'Please check the highlighted fields'. Fixable in the UI alone.",
  "'What will happen' preview: FUTURE — needs routing + playbook evidence lists from the API.",
  "Newest-first / pinned new alert: UI-only (pin) or FUTURE sort parameter.",
];

export const addAlertScreens: OptionDef[] = [
  {
    id: "a", title: "A · Improved modal", tagline: "stay on the list",
    notes: {
      structure: "Modal with rule dropdown (rule + playbook name), programme dropdown, UTC timestamp with 'Now', optional fields folded. Shows real API messages under each field.",
      emphasis: "Quick, correct entry.",
      pros: ["Smallest change from today", "Dropdowns remove most invalid input", "Inline 409/422 messages"],
      cons: ["Little room to explain consequences", "Rule list source is indirect without an endpoint"],
      suits: "Occasional manual entry.",
      backend: be, now: "Yes (rule list derived from existing alerts).",
    },
    render: () => <AddA state="clean" />,
  },
  {
    id: "a-err", title: "A · Modal, error state", tagline: "409 + 422 shown inline",
    notes: {
      structure: "Same modal after a duplicate ID and an invalid timestamp.",
      emphasis: "Errors at the field, in the API's own words. Nothing saved.",
      pros: ["Uses messages the backend already sends"], cons: ["The API returns 409 and 422 on separate attempts; this shows both at once for review"],
      suits: "—", backend: be, now: "Yes.",
    },
    render: () => <AddA state="errors" />,
  },
  {
    id: "b", title: "B · Dedicated page + preview", tagline: "explain what happens",
    notes: {
      structure: "Full page form with a side panel previewing routing, evidence to be gathered and pattern eligibility. 'Create and investigate' goes straight to the new alert's investigation.",
      emphasis: "Understanding the consequence of adding an alert.",
      pros: ["Great for demos — shows routing working", "Natural flow into the investigation"],
      cons: ["Heavier than a modal", "Preview needs new read endpoints"],
      suits: "Demos, training.",
      backend: be, now: "Form yes; preview no.",
    },
    render: () => <AddB />,
  },
  {
    id: "c", title: "C · Side drawer + pinned result", tagline: "add several in a row",
    notes: {
      structure: "Drawer over the list; on success the new alert is pinned at the top and highlighted; 'Create another' keeps the drawer open.",
      emphasis: "Seeing the new alert immediately.",
      pros: ["Solves 'where did my alert go?' (today it lands on the last page)", "Good for adding several test alerts"],
      cons: ["Pinning is UI-only state"],
      suits: "Building demo scenarios.",
      backend: be, now: "Yes.",
    },
    render: () => <AddC />,
  },
];
