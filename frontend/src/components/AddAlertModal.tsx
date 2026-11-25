import { useEffect, useMemo, useRef, useState } from "react";
import { createAlert, getAlertFormOptions } from "../api/alerts";
import { ApiError } from "../api/client";
import { Dropdown } from "./Dropdown";
import { errorText } from "./ui";
import { human, istNow, istToUtcIso } from "../lib/format";
import type { Alert, AlertCreate, AlertFormOptions } from "../api/types";

// Every choice comes from GET /alerts/form-options: rules from the knowledge
// base routing table, customers and programmes from the dataset. Nothing is
// typed free-hand except the alert ID, which is suggested and editable.
//
// Not offered, on purpose: programme (derived from the customer, by the
// server), shared_match_key (supplied upstream) and cluster_ref (the cluster
// engine decides membership; the API refuses a value).

type Field = "alert_id" | "rule_id" | "customer_ref" | "programme_ref" | "created_at";

export function AddAlertModal({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: (a: Alert) => void;
}) {
  const [opts, setOpts] = useState<AlertFormOptions | null>(null);
  const [optsError, setOptsError] = useState<unknown>(null);

  const [alertId, setAlertId] = useState("");
  const idEdited = useRef(false); // once the analyst types, their value is kept
  const [ruleId, setRuleId] = useState("");
  const [customerRef, setCustomerRef] = useState("");
  // Detected At defaults to now, in IST; the analyst can change it.
  const [date, setDate] = useState(() => istNow().date);
  const [time, setTime] = useState(() => istNow().time);

  const [tried, setTried] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [serverErrors, setServerErrors] = useState<Partial<Record<Field, string>>>({});

  useEffect(() => {
    let live = true;
    getAlertFormOptions()
      .then((o) => {
        if (!live) return;
        setOpts(o);
        if (!idEdited.current) setAlertId(o.next_alert_id);
      })
      .catch((e) => live && setOptsError(e));
    return () => {
      live = false;
    };
  }, []);

  const customer = opts?.customers.find((c) => c.customer_ref === customerRef) ?? null;
  const createdAt = istToUtcIso(date, time);

  // Immediate checks. The server repeats every one of them and is the authority.
  const errors = useMemo(() => {
    const e: Partial<Record<Field, string>> = {};
    const id = alertId.trim();
    if (!id) e.alert_id = "Alert ID is required.";
    else if (opts?.alert_ids.includes(id)) e.alert_id = `${id} is already used by another alert. Choose a different ID.`;
    if (!ruleId) e.rule_id = "Choose a rule.";
    if (!customerRef) e.customer_ref = "Choose a customer.";
    if (!date || !time) e.created_at = "Enter the date and time the alert was detected, in IST.";
    else if (!createdAt) e.created_at = "Enter a valid date and time.";
    return e;
  }, [alertId, opts, ruleId, customerRef, date, time, createdAt]);

  // The alert ID is checked as it is typed; the rest once Create is pressed.
  const shownError = (f: Field): string | undefined =>
    serverErrors[f] ?? (tried || (f === "alert_id" && idEdited.current) ? errors[f] : undefined);

  const clearServer = (...fs: Field[]) =>
    setServerErrors((s) => {
      const next = { ...s };
      for (const f of fs) delete next[f];
      return next;
    });

  async function submit() {
    setTried(true);
    if (Object.keys(errors).length || !opts) {
      setError(new ApiError(422, "Please check the highlighted fields."));
      return;
    }
    const body: AlertCreate = {
      alert_id: alertId.trim(),
      rule_id: ruleId,
      customer_ref: customerRef,
      created_at: createdAt!,
    };
    setSubmitting(true);
    setError(null);
    setServerErrors({});
    try {
      onCreated(await createAlert(body));
    } catch (e) {
      setError(e);
      if (e instanceof ApiError && e.status === 409)
        setServerErrors({ alert_id: "This Alert ID already exists. Choose a different ID." });
      if (e instanceof ApiError && e.fieldErrors)
        setServerErrors(
          Object.fromEntries(e.fieldErrors.map((f) => [f.field, f.message.replace(/^Value error, /, "")])),
        );
    } finally {
      setSubmitting(false);
    }
  }

  const banner = optsError
    ? { title: "The form options could not be loaded.", body: errorText(optsError).title }
    : error
      ? errorText(error)
      : null;
  const busy = submitting || !opts;

  const fieldError = (f: Field) => {
    const m = shownError(f);
    return m ? (
      <span className="field-err" id={`f-${f}-err`} role="alert">
        {m}
      </span>
    ) : null;
  };

  return (
    <div className="modal-bg" role="dialog" aria-label="Add new alert">
      <div className="modal">
        <header>Add new alert</header>
        <div className="body">
          {banner ? (
            <div className="err-box" role="alert">
              <strong>{banner.title}</strong>
              {banner.body}
            </div>
          ) : null}

          <div className="field">
            <label htmlFor="f-alert_id">Alert ID</label>
            <input
              id="f-alert_id"
              aria-label="Alert ID"
              className={shownError("alert_id") ? "err" : ""}
              value={alertId}
              placeholder={opts ? opts.next_alert_id : "Loading…"}
              disabled={submitting}
              aria-describedby={shownError("alert_id") ? "f-alert_id-err" : undefined}
              onChange={(e) => {
                idEdited.current = true;
                setAlertId(e.target.value);
                clearServer("alert_id");
                if (error instanceof ApiError && error.status === 409) setError(null);
              }}
            />
            <span className="field-hint">Automatically generated · editable</span>
            {fieldError("alert_id")}
          </div>

          <div className="field">
            <label htmlFor="f-rule_id">Rule</label>
            <Dropdown
              id="f-rule_id"
              ariaLabel="Rule"
              searchable
              value={ruleId}
              invalid={!!shownError("rule_id")}
              placeholder={opts ? "Choose a rule" : "Loading…"}
              disabled={busy}
              groups={[
                {
                  label: "Rules in the knowledge base",
                  options: (opts?.rules ?? []).map((r) => ({
                    value: r.rule_id,
                    label: `${r.rule_id} · ${human(r.category)}`,
                  })),
                },
              ]}
              onChange={(v) => {
                setRuleId(v);
                clearServer("rule_id");
              }}
            />
            {fieldError("rule_id")}
          </div>

          <div className="field">
            <label htmlFor="f-customer_ref">Customer</label>
            <Dropdown
              id="f-customer_ref"
              ariaLabel="Customer"
              searchable
              value={customerRef}
              invalid={!!shownError("customer_ref")}
              placeholder={opts ? "Choose a customer" : "Loading…"}
              disabled={busy}
              groups={[
                {
                  label: "Customers in the dataset",
                  options: (opts?.customers ?? []).map((c) => ({
                    value: c.customer_ref,
                    label: c.customer_ref,
                    hint: [c.name, c.programme_ref].filter(Boolean).join(" · "),
                  })),
                },
              ]}
              onChange={(v) => {
                setCustomerRef(v);
                clearServer("customer_ref", "programme_ref");
              }}
            />
            {fieldError("customer_ref")}
          </div>

          <div className="field">
            <span className="field-label" id="f-programme-label">
              Programme
            </span>
            <div className="readonly" aria-labelledby="f-programme-label" data-testid="programme">
              {customer?.programme_ref ? (
                <span className="mono">{customer.programme_ref}</span>
              ) : (
                <span className="dd-ph">Choose a customer first</span>
              )}
            </div>
            <span className="field-hint">Read-only · derived from customer</span>
            {fieldError("programme_ref")}
          </div>

          <div className="field">
            <span className="field-label">Detected At</span>
            <div className="dt-row">
              <input
                type="date"
                aria-label="Detected date (IST)"
                className={shownError("created_at") ? "err" : ""}
                value={date}
                disabled={submitting}
                onChange={(e) => {
                  setDate(e.target.value);
                  clearServer("created_at");
                }}
              />
              <input
                type="time"
                aria-label="Detected time (IST)"
                className={shownError("created_at") ? "err" : ""}
                value={time}
                disabled={submitting}
                onChange={(e) => {
                  setTime(e.target.value);
                  clearServer("created_at");
                }}
              />
              <span className="tz">IST (UTC+05:30)</span>
            </div>
            <span className="field-hint" data-testid="utc-preview">
              Defaults to now · editable{createdAt ? ` · stored as ${createdAt} (UTC)` : ""}
            </span>
            {fieldError("created_at")}
          </div>
        </div>
        <footer>
          <button className="btn" onClick={onClose} disabled={submitting}>
            Cancel
          </button>
          <button className="btn btn-p" onClick={submit} disabled={busy}>
            {submitting ? "Creating alert…" : "Create alert"}
          </button>
        </footer>
      </div>
    </div>
  );
}
