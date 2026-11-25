"""
Cluster routing and the AI cluster summary.

  - POST /clusters/summary: the model gets only deterministic cluster data and
    the KB cluster purpose/boundaries; its answer must be grounded in that data
    and carry no routing, decision or suspicion; missing key, API failure and
    malformed output give a controlled 200.
  - PUT /clusters/routing: routes every current member, takes precedence over
    earlier individual decisions without deleting them, is overridden by a later
    individual decision, follows every member's allowed_outcomes, survives a
    restart, and is never applied to a changed membership.

Every model call is a fake. The canonical dataset is read-only here; the
membership-change part runs against a temporary copy (UC3_DATASET_PATH).
Routing goes to temporary files (UC3_ROUTING_PATH). Exit code 0 on success.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

TMP = Path(tempfile.mkdtemp(prefix="uc3-cluster-routing-"))
os.environ["UC3_ROUTING_PATH"] = str(TMP / "routing.json")
os.environ["UC3_AI_CACHE_PATH"] = str(TMP / "ai_cache.json")

import yaml  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend.ai import AIAnalyst  # noqa: E402
from backend.ai.cluster_summary import FIELDS, check_cluster_output, cluster_output_schema  # noqa: E402
from backend.api import app  # noqa: E402
from backend.paths import DATASET_PATH, KB_DIR, PROJECT_ROOT  # noqa: E402
from backend.routing import DESTINATIONS  # noqa: E402

passed: list[str] = []
failed: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    (passed if cond else failed).append(f"{name}{(' — ' + detail) if detail else ''}")


class Fake:
    def __init__(self, behaviour):
        self.behaviour = behaviour
        self.calls: list[dict] = []
        self.responses = self

    def create(self, **kw):
        self.calls.append(kw)
        return self.behaviour(kw)


def returns(out, status="completed"):
    text = out if isinstance(out, str) else json.dumps(out)
    return lambda kw: SimpleNamespace(status=status, output_text=text)


def grounded(payload: dict) -> dict:
    """A well-behaved explanation, written only from the payload it was sent."""
    c, cr = payload["cluster"], payload["criteria"]
    items = []
    for name, v in cr.items():
        if name == "rule_ids":
            items.append({"text": f"Upstream rules: {', '.join(v['actual'])} (met).", "fields": ["criteria.rule_ids"]})
        elif "threshold" in v and "actual" in v:
            items.append({"text": f"{name.replace('_', ' ').capitalize()}: {v['actual']} against a threshold of "
                                  f"{v['threshold']} (met).", "fields": [f"criteria.{name}"]})
        else:
            items.append({"text": f"Time span: {v['actual_span']} hours against a threshold of {v['threshold']} "
                                  f"hours (met).", "fields": [f"criteria.{name}"]})
    return {
        "summary": f"This {c['label']} contains {c['alert_count']} alerts across {c['customer_count']} customers "
                   f"in {', '.join(c['programme_refs'])}.",
        "summary_fields": ["cluster.label", "cluster.alert_count", "cluster.customer_count", "cluster.programme_refs"],
        "criteria_met": items,
        "characteristics": [{"text": f"All alerts come from {', '.join(c['rule_ids'])}.", "fields": ["cluster.rule_ids"]}],
    }


def main() -> int:
    dataset_md5 = hashlib.md5(DATASET_PATH.read_bytes()).hexdigest()
    project_file = PROJECT_ROOT / "data" / "routing.json"

    def project_routing():
        return hashlib.md5(project_file.read_bytes()).hexdigest() if project_file.exists() else None

    routing_before = project_routing()
    cfg = yaml.safe_load((KB_DIR / "config.yaml").read_text())
    pbs = yaml.safe_load((KB_DIR / "playbooks.yaml").read_text())["playbooks"]
    outcomes = cfg["investigation_outcomes"]
    nfa = "no_action_required"

    print("=" * 78)
    print("CLUSTER ROUTING AND AI CLUSTER SUMMARY")
    print("=" * 78)

    with TestClient(app) as c:
        clusters = c.get("/clusters").json()["clusters"]
        alerts = {a["alert_id"]: a for a in c.get("/alerts?limit=500").json()["alerts"]}

        def permits_nfa(cl):
            return all(nfa in pbs[cfg["routing"][alerts[a]["rule_id"]]]["allowed_outcomes"] for a in cl["alert_ids"])

        closed_cl = next(x for x in clusters if not permits_nfa(x))
        open_cl = next(x for x in clusters if permits_nfa(x))
        ids = closed_cl["alert_ids"]

        def use(behaviour):
            fake = Fake(behaviour)
            app.state.ai_analyst = AIAnalyst(fake, "test-model-from-env", outcomes)
            app.state.ai_cache.clear()  # each fake answers afresh; caching is tested in test_ai_cache.py
            return fake

        # ------------------------------------------------ AI summary input
        print("\n[1] AI cluster summary: input is deterministic cluster data only")
        fake = use(lambda kw: returns(grounded(json.loads(kw["input"])))(kw))
        r = c.post("/clusters/summary", json={"alert_ids": ids})
        sent = json.loads(fake.calls[0]["input"])
        s = r.json()["ai_summary"]
        check("200 generated, labelled AI-generated, model from configuration",
              r.status_code == 200 and s["status"] == "generated" and s["label"] == "AI-generated"
              and s["model"] == "test-model-from-env" and fake.calls[0]["model"] == "test-model-from-env", str(s))
        check("payload sections", set(sent) == {"cluster", "criteria", "grouping", "members", "guidance"})
        check("cluster values equal GET /clusters",
              sent["cluster"]["alert_count"] == len(ids) and sent["cluster"]["member_alert_ids"] == ids
              and sent["cluster"]["customer_count"] == len(closed_cl["customer_refs"])
              and sent["cluster"]["time_span_hours"] == closed_cl["time_span_hours"]
              and sent["cluster"]["rule_ids"] == closed_cl["rule_ids"])
        check("criteria carry the configured thresholds and actual values",
              sent["criteria"]["min_alerts"] == {"threshold": closed_cl["thresholds"]["min_alerts"],
                                                 "actual": len(ids), "met": True}
              and sent["criteria"]["min_customers"]["threshold"] == closed_cl["thresholds"]["min_customers"])
        kb = yaml.safe_load((KB_DIR / "clusters" / f"{closed_cl['cluster_type']}.yaml").read_text())
        check("guidance is the KB purpose and boundaries only",
              sent["guidance"] == {"purpose": kb["purpose"].strip(), "boundaries": kb["boundaries"]})
        flat = json.dumps(sent)
        check("KB analyst_guidance (routing advice) and interpretation_guidance are not sent",
              all(g not in flat for g in kb["analyst_guidance"]) and kb["interpretation_guidance"].strip() not in flat)
        check("members carry alert records only, no evidence",
              all(set(m) == {"alert_id", "rule_id", "customer_ref", "programme_ref", "created_at"} for m in sent["members"]))
        check("strict schema; citable fields are the payload's own paths",
              fake.calls[0]["text"]["format"]["strict"] is True
              and cluster_output_schema()["properties"]["summary_fields"]["items"]["enum"] == list(FIELDS))
        check("response has no routing, outcome, decision or confidence field",
              not ({"destination", "outcome", "recommended_outcome", "decision", "confidence", "routing"}
                   & (set(s) | set(r.json()))))

        # ------------------------------------------------ AI summary checks
        print("[2] AI cluster summary: grounded, no routing, no suspicion")
        good = grounded(sent)
        check("grounded explanation accepted", check_cluster_output(good, sent, [v[0] for v in DESTINATIONS.values()], outcomes) == [])
        labels = [v[0] for v in DESTINATIONS.values()]
        bad_texts = [
            ("routes to a team", "This cluster should be routed to the Sanctions Analyst."),
            ("names a destination", f"{labels[0]} is the right owner for this cluster."),
            ("routing verb", "These alerts can be sent together for review."),
            ("recommends", "We recommend escalating this cluster."),
            ("outcome name", "Outcome: no action required."),
            ("suspicion", "The grouping suggests suspicion of coordinated activity."),
            ("wrongdoing", "This indicates wrongdoing by the customers."),
            ("judgement", "The alert count is unusually high."),
            ("instruction", "Review each member alert individually."),
            ("invented number", "The alerts arrived 3.5 times faster than normal."),
            ("invented period", "All alerts occurred within 2 days."),
            ("screening match claim", "The screening returned a PEP match for these customers."),
            ("confirmed match", "Each alert is a confirmed sanctions match."),
        ]
        for why, text in bad_texts:
            out = dict(good, characteristics=[{"text": text, "fields": ["cluster.alert_count"]}])
            probs = check_cluster_output(out, sent, labels, outcomes)
            check(f"rejected: {why}", bool(probs), text)
        for why, out in [("uncited statement", dict(good, characteristics=[{"text": "A grouping.", "fields": []}])),
                         ("unknown field", dict(good, summary_fields=["cluster.risk_score"])),
                         ("no criteria", dict(good, criteria_met=[])),
                         ("extra field", {**good, "recommended_destination": "mlro"})]:
            check(f"rejected: {why}", bool(check_cluster_output(out, sent, labels, outcomes)))
        for text in ("The alerts arrived over 99 hours.", f"All {len(ids)} alerts fell within {len(ids)} hours."):
            out = dict(good, characteristics=[{"text": text, "fields": ["cluster.time_span_hours"]}])
            check(f"rejected: duration that is not the span or window: {text!r}",
                  bool(check_cluster_output(out, sent, labels, outcomes)))
        window = closed_cl["thresholds"]["time_window_hours"]
        win_ok = dict(good, characteristics=[{"text": f"The configured window is {window} hours.",
                                              "fields": ["criteria.time_window_hours"]}])
        check("the configured window may be stated", check_cluster_output(win_ok, sent, labels, outcomes) == [])
        rules_ok = dict(good, characteristics=[{"text": f"The upstream rules {', '.join(closed_cl['rule_ids'])} matched the configured rules.",
                                                "fields": ["criteria.rule_ids"]}])
        check("alerts matching the configured rules may be stated",
              check_cluster_output(rules_ok, sent, labels, outcomes) == [],
              str(check_cluster_output(rules_ok, sent, labels, outcomes)))
        span_ok = dict(good, characteristics=[{"text": f"The cluster spans {closed_cl['time_span_hours']} hours.",
                                               "fields": ["cluster.time_span_hours"]}])
        check("the computed span may be stated", check_cluster_output(span_ok, sent, labels, outcomes) == [])

        print("[3] AI cluster summary: failures are controlled")
        app.state.ai_analyst = AIAnalyst.from_env(outcomes, environ={})
        s = c.post("/clusters/summary", json={"alert_ids": ids}).json()["ai_summary"]
        check("no key: unavailable, no content", s["status"] == "unavailable" and s["summary"] is None
              and "OPENAI_API_KEY" in s["reason"], str(s))
        use(lambda kw: (_ for _ in ()).throw(RuntimeError("down")))
        s = c.post("/clusters/summary", json={"alert_ids": ids}).json()["ai_summary"]
        check("API failure: error, no content", s["status"] == "error" and "RuntimeError" in s["reason"]
              and s["criteria_met"] == [])
        for label, out in [("not JSON", "a cluster"), ("wrong shape", {"summary": "x"}),
                           ("routing recommendation", dict(good, summary="Route this cluster to the MLRO / Nominated Officer."))]:
            use(returns(out))
            s = c.post("/clusters/summary", json={"alert_ids": ids}).json()["ai_summary"]
            check(f"invalid output ({label}): error, nothing shown", s["status"] == "error" and s["summary"] is None)
        r = c.post("/clusters/summary", json={"alert_ids": ids[:-1]})
        check("not a currently detected cluster: 409, model not called", r.status_code == 409)

        # ------------------------------------------------ cluster routing
        print("[4] cluster routing: all current members, precedence, persistence")
        first, second = ids[0], ids[1]
        c.put(f"/alerts/{first}/routing", json={"destination": "kyb_cdd"})  # an earlier individual decision
        r = c.put("/clusters/routing", json={"alert_ids": ids, "destination": "mlro"})
        rec = r.json()
        check("PUT /clusters/routing 200 with the exact membership key",
              r.status_code == 200 and rec["alert_ids"] == sorted(ids)
              and rec["key"] == f"{closed_cl['cluster_type']}:{','.join(sorted(ids))}" and rec["destination"] == "mlro",
              str(rec))
        eff = c.get("/routing").json()
        check("every current member is routed via the cluster",
              all(eff["routing"][a]["destination"] == "mlro" and eff["routing"][a]["source"] == "cluster"
                  and eff["routing"][a]["cluster_label"] == closed_cl["label"] for a in ids))
        raw = json.loads((TMP / "routing.json").read_text())
        check("the earlier individual decision is kept, not deleted",
              raw["alerts"][first]["destination"] == "kyb_cdd" and len(raw["clusters"]) == 1)
        check("cluster decision listed in GET /routing", [x["key"] for x in eff["clusters"]] == [rec["key"]])
        c.put(f"/alerts/{second}/routing", json={"destination": "programme_operations"})
        eff = c.get("/routing").json()["routing"]
        check("a later individual decision overrides the cluster for that alert only",
              eff[second]["destination"] == "programme_operations" and eff[second]["source"] == "alert"
              and all(eff[a]["source"] == "cluster" for a in ids if a != second))
        r = c.put("/clusters/routing", json={"alert_ids": ids, "destination": "sanctions_analyst"})
        eff = c.get("/routing").json()["routing"]
        check("re-routing the cluster applies to every member again",
              r.status_code == 200 and all(eff[a]["destination"] == "sanctions_analyst" for a in ids))
        r = c.put("/clusters/routing", json={"alert_ids": ids, "outcome": nfa})
        check("outcome refused when a member's playbook does not permit it (409)",
              r.status_code == 409 and "not permitted" in r.json()["detail"])
        r = c.put("/clusters/routing", json={"alert_ids": open_cl["alert_ids"], "outcome": nfa})
        check("outcome accepted when every member's playbook permits it",
              r.status_code == 200 and r.json()["outcome"] == nfa)
        for label, body in [("not a cluster", {"alert_ids": ids[:2], "destination": "mlro"}),
                            ("both", {"alert_ids": ids, "destination": "mlro", "outcome": nfa}),
                            ("neither", {"alert_ids": ids}), ("empty members", {"alert_ids": [], "destination": "mlro"}),
                            ("unknown destination", {"alert_ids": ids, "destination": "ai"})]:
            r = c.put("/clusters/routing", json=body)
            check(f"{label}: refused", r.status_code in (409, 422), str(r.status_code))
        alert_before = c.get(f"/alerts/{first}").json()

    print("[5] survives a restart")
    with TestClient(app) as c2:
        eff = c2.get("/routing").json()["routing"]
        check("cluster routing survives a restart",
              all(eff[a]["destination"] == "sanctions_analyst" and eff[a]["source"] == "cluster" for a in ids))
        check("alert records unchanged by routing", c2.get(f"/alerts/{first}").json() == alert_before
              and "rout" not in json.dumps(alert_before))

    print("[6] a changed membership does not inherit the old routing")
    copy_dir = TMP / "data-copy"
    copy_dir.mkdir()
    shutil.copy(DATASET_PATH, copy_dir / "dataset.json")
    os.environ["UC3_DATASET_PATH"] = str(copy_dir / "dataset.json")
    os.environ["UC3_ROUTING_PATH"] = str(copy_dir / "routing.json")
    try:
        with TestClient(app) as c3:
            vel = next(x for x in c3.get("/clusters").json()["clusters"] if x["cluster_type"] == "repeated_velocity")
            old_ids = vel["alert_ids"]
            check("route the cluster as it is now", c3.put("/clusters/routing",
                  json={"alert_ids": old_ids, "destination": "mlro"}).status_code == 200)
            member = c3.get(f"/alerts/{old_ids[0]}").json()
            new = {**member, "alert_id": "ALRT-T-9001", "created_at": member["created_at"]}
            check("a new alert that joins the cluster is added", c3.post("/alerts", json=new).status_code == 201)
            now = next(x for x in c3.get("/clusters").json()["clusters"] if x["cluster_type"] == "repeated_velocity")
            check("the detected membership changed", set(now["alert_ids"]) == set(old_ids) | {"ALRT-T-9001"},
                  str(now["alert_ids"]))
            r = c3.get("/routing").json()
            check("the new member is not routed", "ALRT-T-9001" not in r["routing"])
            new_key = f"repeated_velocity:{','.join(sorted(now['alert_ids']))}"
            check("the new membership is an unrouted cluster", all(x["key"] != new_key for x in r["clusters"]))
            check("the original members keep the decision made for them",
                  all(r["routing"][a]["source"] == "cluster" for a in old_ids))
            check("the old membership can no longer be routed (not currently detected)",
                  c3.put("/clusters/routing", json={"alert_ids": old_ids, "destination": "kyb_cdd"}).status_code == 409)
    finally:
        os.environ.pop("UC3_DATASET_PATH", None)

    print("[7] a first-version routing file is still read")
    legacy = TMP / "legacy" / "routing.json"
    legacy.parent.mkdir()
    legacy.write_text(json.dumps({"ALRT-0001": {"alert_id": "ALRT-0001", "destination": "mlro", "outcome": None,
                                                "routed_at": "2026-10-05T06:29:14Z"}}))
    os.environ["UC3_ROUTING_PATH"] = str(legacy)
    with TestClient(app) as c4:
        check("flat-format decision is read", c4.get("/routing").json()["routing"]["ALRT-0001"]["destination"] == "mlro")
        c4.put("/alerts/ALRT-0002/routing", json={"destination": "kyb_cdd"})
        raw = json.loads(legacy.read_text())
        check("rewritten in the new shape, keeping the old decision",
              set(raw["alerts"]) == {"ALRT-0001", "ALRT-0002"} and "clusters" in raw)

    print("[8] the canonical dataset is never written")
    check("dataset.json unchanged", hashlib.md5(DATASET_PATH.read_bytes()).hexdigest() == dataset_md5)
    check("the project routing file is untouched", project_routing() == routing_before)

    print()
    for f in failed:
        print("  FAIL", f)
    print(f"\n{len(passed)} passed, {len(failed)} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
