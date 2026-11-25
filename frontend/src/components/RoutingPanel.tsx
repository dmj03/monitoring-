import { useEffect, useState } from "react";
import { ApiError } from "../api/client";
import { ErrorBox } from "./ui";
import { Dropdown } from "./Dropdown";
import { fmtDate } from "../lib/format";
import type { RoutingView } from "../lib/hooks";
import type { RoutingList } from "../api/types";

// A dropdown value encodes which list the choice came from: "destination:<id>" or "outcome:<id>".
const encode = (type: "destination" | "outcome", id: string) => `${type}:${id}`;
type Body = { destination: string } | { outcome: string };

/**
 * The analyst's routing action, for one alert or for a cluster. Options come
 * from GET /routing; whether the outcome is available comes from the playbooks'
 * allowed_outcomes, passed in by the caller. Choosing an option only selects it;
 * nothing is recorded until the analyst presses the button. The form stays
 * visible after routing, preselected with the current decision. The AI plays no part.
 */
export function RoutingPanel({
  id,
  noun,
  current,
  routing,
  failed,
  allowedOutcomes,
  blockedReason,
  note,
  submit,
}: {
  id: string;
  noun: "alert" | "cluster";
  current: RoutingView;
  routing: RoutingList | null;
  failed: boolean;
  /** Outcome ids permitted here; null while still being determined. */
  allowedOutcomes: string[] | null;
  /** Why an outcome is not available, e.g. "the X playbook does not permit it". */
  blockedReason: string;
  /** Optional line shown under the form. */
  note?: string;
  submit: (body: Body) => Promise<void>;
}) {
  const currentValue = current.record
    ? current.record.destination
      ? encode("destination", current.record.destination)
      : encode("outcome", current.record.outcome!)
    : "";
  const [value, setValue] = useState(currentValue);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [justSaved, setJustSaved] = useState(false);

  // Follow the stored decision when it loads or changes.
  useEffect(() => setValue(currentValue), [currentValue]);

  if (!routing)
    return <p className="dim">{failed ? "Routing is not available right now." : "Loading routing options…"}</p>;

  const [type, optId] = value ? (value.split(":") as ["destination" | "outcome", string]) : [null, null];
  const selected =
    type === "destination"
      ? routing.destinations.find((d) => d.id === optId)
      : type === "outcome"
        ? routing.outcomes.find((o) => o.id === optId)
        : undefined;
  const changed = value !== currentValue;
  const permitted = (o: string) => !!allowedOutcomes && allowedOutcomes.includes(o);
  const blocked = routing.outcomes.filter((o) => !permitted(o.id));
  const Noun = noun === "alert" ? "Alert" : "Cluster";

  async function onSubmit() {
    if (!type || !optId) return;
    setSaving(true);
    setError(null);
    setJustSaved(false);
    try {
      await submit(type === "destination" ? { destination: optId } : { outcome: optId });
      setJustSaved(true);
    } catch (e) {
      setError(e);
    } finally {
      setSaving(false);
    }
  }

  const routed = current.kind === "destination";
  return (
    <div className="route-box">
      <div className="route-status" role="status" aria-label="Routing decision">
        {current.kind === "none" ? (
          <>
            <span className="routed-k">Status</span>
            <span className="routed-v dim-v">Not routed</span>
          </>
        ) : (
          <>
            <div>
              <span className="routed-k">{routed ? "Routed to" : "Outcome"}</span>
              <span className="routed-v">{current.label}</span>
            </div>
            <div>
              <span className="routed-k">Status</span>
              <span className={`pill ${routed ? "pill-route" : "pill-closed"}`}>{routed ? "Routed" : "Closed"}</span>
            </div>
            {current.via ? (
              <div>
                <span className="routed-k">Source</span>
                <span className="routed-when">Via cluster · {current.via}</span>
              </div>
            ) : null}
            <div>
              <span className="routed-k">Recorded</span>
              <span className="routed-when">{fmtDate(current.record!.routed_at)}</span>
            </div>
          </>
        )}
      </div>

      {justSaved && !changed ? (
        <div className="routed-confirm">
          ✓ {routed ? `${Noun} routed to ${current.label}.` : `Outcome recorded: ${current.label}.`}{" "}
          {noun === "cluster"
            ? "Every current member alert now uses this routing."
            : "The evidence and AI summary above are unchanged."}
        </div>
      ) : null}

      <div className="route-form">
        <label className="route-label" htmlFor={`route-${id}`}>
          {current.kind === "none" ? `Where should this ${noun} be routed?` : "Change routing"}
        </label>
        <div className="route-row">
          <Dropdown
            id={`route-${id}`}
            ariaLabel="Routing destination"
            value={value}
            disabled={saving}
            placeholder="Select a destination…"
            onChange={(v) => {
              setValue(v);
              setJustSaved(false);
              setError(null);
            }}
            groups={[
              {
                label: "Route to",
                options: routing.destinations.map((d) => ({
                  value: encode("destination", d.id),
                  label: d.label,
                  hint: d.description,
                })),
              },
              {
                label: "Outcome",
                options: routing.outcomes.map((o) => ({
                  value: encode("outcome", o.id),
                  label: o.label,
                  hint: permitted(o.id)
                    ? o.description
                    : allowedOutcomes
                      ? `Not permitted by ${noun === "alert" ? "this playbook" : "every member's playbook"}`
                      : "Checking the member playbooks…",
                  disabled: !permitted(o.id),
                })),
              },
            ]}
          />
          <button className="btn btn-p" disabled={!value || !changed || saving} onClick={() => void onSubmit()}>
            {saving
              ? "Recording…"
              : type === "outcome"
                ? "Record outcome"
                : current.kind === "none"
                  ? `Route ${noun}`
                  : "Update routing"}
          </button>
        </div>
        <p className="fine">
          {selected && changed
            ? `${selected.label}: ${selected.description}. Nothing is recorded until you press the button.`
            : selected
              ? `${selected.label}: ${selected.description}.`
              : `Choose where to send this ${noun}, or close it with an outcome. The AI does not choose a destination.`}
        </p>
        {blocked.length && allowedOutcomes ? (
          <p className="fine">
            {blocked.map((o) => o.label).join(", ")} is not available: {blockedReason}.
          </p>
        ) : null}
        {note ? <p className="fine">{note}</p> : null}
      </div>

      {error instanceof ApiError ? (
        <div className="err-box" role="alert">
          <strong>Routing not recorded</strong>
          {error.detail}
        </div>
      ) : error ? (
        <ErrorBox error={error} />
      ) : null}
    </div>
  );
}
