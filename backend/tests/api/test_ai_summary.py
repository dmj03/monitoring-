"""
AI analyst summary tests.

Every model call is a fake: FakeClient stands in for openai.OpenAI and records
what it was sent. No test reaches the network or needs OPENAI_API_KEY.

What is proven:
  - the model receives the real evidence pack (values, status, provenance) and
    nothing that would invite a decision
  - the model cannot cite evidence that does not exist
  - MISSING evidence cannot be turned into a negative, false or failed conclusion
  - decision, recommendation, confidence and time-period language is rejected
  - the summary is a natural narrative that begins with the alert type; its
    sentences are accepted when every claim is grounded (values stated with all
    their words, named items cited, MISSING items named in their own sentence)
    and rejected for unsupported relationships between numbers, thresholds,
    causes, numbers in words or "Name: value" lists
  - missing key, API failure and malformed output give a controlled state
  - POST /investigate is unchanged and never depends on the AI
  - no secrets, no hardcoded alert facts, one place that imports openai

Uses the canonical dataset read-only, in-process. Plain-script style like the
other suites. Exit code 0 on success.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import os  # noqa: E402
import tempfile  # noqa: E402

# Cached summaries go to a temporary file, never into the project's data/.
os.environ["UC3_AI_CACHE_PATH"] = str(Path(tempfile.mkdtemp(prefix="uc3-ai-summary-")) / "ai_cache.json")

import yaml  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend.ai import AIAnalyst, build_summary_input, check_output  # noqa: E402
from backend.ai.analyst import API_KEY_ENV, MODEL_ENV  # noqa: E402
from backend.api import app  # noqa: E402
from backend.paths import DATASET_PATH, KB_DIR, PROJECT_ROOT  # noqa: E402

passed: list[str] = []
failed: list[str] = []

ALERT_ID = "ALRT-0047"  # RG-GEO-031, one MISSING item (prior_alerts)
TEST_MODEL = "test-model-from-env"


def check(name: str, cond: bool, detail: str = "") -> None:
    (passed if cond else failed).append(f"{name}{(' — ' + detail) if detail else ''}")


class FakeResponses:
    def __init__(self, behaviour):
        self.behaviour = behaviour
        self.calls: list[dict] = []

    def create(self, **kw):
        self.calls.append(kw)
        return self.behaviour(kw)


class FakeClient:
    def __init__(self, behaviour):
        self.responses = FakeResponses(behaviour)


def returns(out, status: str = "completed"):
    text = out if isinstance(out, str) else json.dumps(out)
    return lambda kw: SimpleNamespace(status=status, output_text=text)


def raises(exc: Exception):
    def b(kw):
        raise exc
    return b


def readable(name: str) -> str:
    return name.replace("_", " ")


def state(e: dict) -> str:
    """One FOUND item in natural words, stating its value as the checks require."""
    name, v = readable(e["field"]), e["value"]
    if isinstance(v, bool):
        return f"{name} is recorded as {str(v).lower()}"
    if isinstance(v, list):
        return f"{len(v)} {name} entries"
    if isinstance(v, dict):
        return f"{name} is recorded"
    return f"{name} of {readable(v) if isinstance(v, str) else v}"


def finding(k: str, e: dict) -> dict:
    v = e["value"]
    shown = (str(v).lower() if isinstance(v, bool) else len(v) if isinstance(v, list)
             else readable(v) if isinstance(v, str) else v)
    return {"text": f"{readable(e['field']).capitalize()}: {shown}", "evidence_keys": [k]}


def grounded_output(payload: dict) -> dict:
    """
    A well-behaved model answer, written only from the payload it was sent, so
    every value in it is provably taken from the evidence pack: the alert type,
    one sentence stating every FOUND value, one naming every MISSING item.
    """
    ev = payload["evidence"]
    found = [k for k, v in ev.items() if v["status"] == "FOUND"]
    missing = [k for k, v in ev.items() if v["status"] == "MISSING"]
    sentences = [{"text": f"An alert for {readable(payload['playbook']['category'])} has been raised.",
                  "evidence_keys": []}]
    if found:
        sentences.append({"text": "The available data shows "
                          + ", ".join(state(ev[k]) for k in found) + ".", "evidence_keys": found})
    if missing:
        sentences.append({"text": f"{', '.join(readable(k) for k in missing).capitalize()} "
                                  f"{'is' if len(missing) == 1 else 'are'} not available in the "
                                  f"collected evidence.", "evidence_keys": missing})
    findings = [finding(k, ev[k]) for k in found if not isinstance(ev[k]["value"], (list, dict))][:3]
    findings += [{"text": f"{readable(k).capitalize()}: not available", "evidence_keys": [k]}
                 for k in missing][:max(0, 2 - len(findings))]
    return {
        "summary_sentences": sentences,
        "key_findings": findings,
        # Attention must be a supported observation kind; a MISSING item is the
        # one every payload with a gap supports. With no gap, an empty list.
        "analyst_attention": [
            {"kind": "missing_evidence",
             "text": f"{readable(missing[0]).capitalize()}: not available in the collected evidence.",
             "evidence_keys": [missing[0]]}] if missing else [],
    }


def main() -> int:
    raw = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    dataset_md5 = hashlib.md5(DATASET_PATH.read_bytes()).hexdigest()
    alert = next(a for a in raw["alerts"] if a["alert_id"] == ALERT_ID)
    body = {"alert": {k: v for k, v in alert.items() if not k.startswith("_")}}
    outcomes = yaml.safe_load((KB_DIR / "config.yaml").read_text())["investigation_outcomes"]

    print("=" * 78)
    print("AI ANALYST SUMMARY")
    print("=" * 78)

    with TestClient(app) as client:
        engine = app.state.investigation_engine
        original_analyst = app.state.ai_analyst

        def use(behaviour) -> FakeClient:
            fake = FakeClient(behaviour)
            app.state.ai_analyst = AIAnalyst(fake, TEST_MODEL, outcomes)
            # Each fake gives a new answer for the same input; the cache would
            # otherwise return the previous accepted one. Caching is tested in
            # test_ai_cache.py.
            app.state.ai_cache.clear()
            return fake

        inv_before = client.post("/investigate", json=body).json()
        pack = engine.investigate(body["alert"])
        payload = build_summary_input(engine, pack)

        # ------------------------------------------------ 1. the model input
        print("\n[1] model input is the deterministic evidence pack")
        check("payload has exactly the approved sections",
              set(payload) == {"alert", "playbook", "global_boundaries", "evidence",
                               "missing_evidence"}, str(sorted(payload)))
        check("playbook section carries no procedure or decision material",
              set(payload["playbook"]) == {"playbook_id", "category", "investigation_focus",
                                           "ai_boundaries"}, str(sorted(payload["playbook"])))
        flat = json.dumps(payload)

        def all_keys(v) -> set[str]:
            if isinstance(v, dict):
                return set(v) | {k for x in v.values() for k in all_keys(x)}
            if isinstance(v, list):
                return {k for x in v for k in all_keys(x)}
            return set()

        # Checked as keys: the KB's boundary text itself mentions "allowed_outcomes".
        payload_keys = all_keys(payload)
        for banned in ("permitted_outcomes", "allowed_outcomes", "escalation_guidance",
                       "checklist", "investigation_checklist", "created_at", "model_may",
                       "outcome_contract", "instruction"):
            check(f"payload omits {banned}", banned not in payload_keys)
        for o in outcomes:
            check(f"payload never names outcome {o}", o not in flat)
        check("evidence keys are exactly the pack's keys",
              list(payload["evidence"]) == [e.key for e in pack.evidence])
        api_ev = {e["key"]: e for e in inv_before["evidence"]}
        check("every value, status and source equals POST /investigate",
              all(payload["evidence"][k]["value"] == api_ev[k]["value"]
                  and payload["evidence"][k]["status"] == api_ev[k]["status"]
                  and payload["evidence"][k]["tool"] == api_ev[k]["tool"]
                  and payload["evidence"][k]["field"] == api_ev[k]["field"]
                  for k in api_ev))
        check("every item keeps its tool args",
              all(payload["evidence"][e.key]["args"] == e.args for e in pack.evidence))
        check("FOUND values carried verbatim (real dataset values)",
              payload["evidence"]["country_exposure"] == {
                  "status": "FOUND", "value": "AE", "tool": "get_transaction_summary",
                  "field": "top_country_exposure",
                  "args": {"customer_ref": "CUST-0514", "window_hours": 168}}
              and payload["evidence"]["high_risk_country_volume"]["value"] == 56
              and payload["evidence"]["programme_high_risk_list"]["value"] == ["AE"])
        check("MISSING item is status MISSING with value null, never a guess",
              payload["evidence"]["prior_alerts"]["status"] == "MISSING"
              and payload["evidence"]["prior_alerts"]["value"] is None)
        check("missing_evidence lists exactly the MISSING items",
              payload["missing_evidence"] == pack.missing == ["prior_alerts"])

        # ------------------------------------------------ 2. successful summary
        print("[2] successful summary")
        fake = use(lambda kw: returns(grounded_output(json.loads(kw["input"])))(kw))
        r = client.post("/investigate/summary", json=body)
        s = r.json()["ai_summary"]
        sent = fake.responses.calls[0]
        check("200", r.status_code == 200, str(r.status_code))
        check("status generated, by llm, labelled AI-generated",
              s["status"] == "generated" and s["generated_by"] == "llm"
              and s["label"] == "AI-generated", str(s.get("reason")))
        check("model name comes from configuration, not code",
              sent["model"] == TEST_MODEL and s["model"] == TEST_MODEL)
        check("model was sent exactly build_summary_input()",
              json.loads(sent["input"]) == payload)
        check("strict structured output requested",
              sent["text"]["format"]["type"] == "json_schema"
              and sent["text"]["format"]["strict"] is True)
        props = sent["text"]["format"]["schema"]["properties"]
        check("schema asks for the narrative as cited sentences",
              set(props) == {"summary_sentences", "key_findings", "analyst_attention"}, str(sorted(props)))
        enum = props["summary_sentences"]["items"]["properties"]["evidence_keys"]["items"]["enum"]
        check("schema only allows citing the pack's own keys",
              enum == [e.key for e in pack.evidence])
        check("summary is the sentences joined, beginning with the alert type",
              s["summary"] == " ".join(x["text"] for x in s["summary_sentences"])
              and s["summary"].startswith("An alert for high risk geography exposure has been raised."),
              s["summary"])
        check("summary_evidence_keys are the sentences' citations, in order",
              s["summary_evidence_keys"] == [k for x in s["summary_sentences"] for k in x["evidence_keys"]])
        check("evidence values appear in the summary as supplied",
              "AE" in s["summary"] and "56" in s["summary"])
        check("MISSING item reported as not available, in the narrative and as missing_information",
              s["missing_information"] == [{"text": "Prior alerts is not available in the "
                                                    "collected evidence.",
                                            "evidence_keys": ["prior_alerts"]}]
              and s["missing_information"][0] in s["summary_sentences"], str(s["missing_information"]))
        basis = {b["key"]: b for b in s["evidence_basis"]}
        check("evidence_basis is the investigation's own record of each cited key",
              all(basis[k] == {kk: api_ev[k][kk] for kk in
                               ("key", "status", "value", "tool", "field")} for k in basis)
              and "prior_alerts" in basis)
        check("response has no decision, outcome or confidence field",
              not ({"confidence", "outcome", "recommended_outcome", "decision",
                    "escalation"} & (set(s) | set(r.json()))))

        # ------------------------------------------------ 3. citation grounding
        print("[3] the AI cannot cite evidence that does not exist")
        good = grounded_output(payload)
        sentences, findings = good["summary_sentences"], good["key_findings"]

        def rejected(out) -> dict:
            use(returns(out))
            return client.post("/investigate/summary", json=body).json()["ai_summary"]

        def with_sentence(text: str, keys: list[str], at: int = 1) -> dict:
            """The good answer with one narrative sentence replaced."""
            ss = list(sentences)
            ss[at] = {"text": text, "evidence_keys": keys}
            return dict(good, summary_sentences=ss)

        def with_finding(text: str, keys: list[str]) -> dict:
            """The good answer with its first key finding replaced."""
            return dict(good, key_findings=[{"text": text, "evidence_keys": keys}, *findings[1:]])

        for where in ("summary_sentences", "key_findings", "analyst_attention"):
            bad = json.loads(json.dumps(good))
            bad[where][-1:] = [{"text": "Sanctions hit is not available in the collected evidence.",
                                "evidence_keys": ["sanctions_hit"]}]
            res = rejected(bad)
            check(f"invented key in {where} rejected",
                  res["status"] == "error" and "does not exist" in res["reason"]
                  and res["key_findings"] == [] and res["summary"] is None, res["reason"])
        bad = with_finding("Top country exposure: AE", [])
        check("uncited statement rejected", rejected(bad)["status"] == "error")
        res = rejected(with_finding("Prior alerts: 2", ["prior_alerts"]))
        check("MISSING item given a value as a finding rejected",
              res["status"] == "error" and "not available" in res["reason"], res["reason"])
        res = rejected(with_finding("KYB status: not available", ["kyb_status"]))
        check("FOUND item presented as missing rejected",
              res["status"] == "error" and "does not state its value" in res["reason"], res["reason"])
        res = rejected(dict(good, summary_sentences=sentences[:2]))
        check("omitting a MISSING item from the narrative rejected",
              res["status"] == "error" and "omits MISSING" in res["reason"], res["reason"])
        res = rejected(with_finding("High risk country transaction count: 1,200",
                                    ["high_risk_country_volume"]))
        check("fabricated number rejected",
              res["status"] == "error" and "1,200" in res["reason"], res["reason"])
        for text in ("The available data shows 56 high risk transactions over the last 7 days.",
                     "The available data shows 56 high risk transactions in the 168-hour window.",
                     "The available data shows 56 high risk transactions per week."):
            res = rejected(with_sentence(text, ["high_risk_country_volume"]))
            check(f"invented time period rejected: {text!r}", res["status"] == "error",
                  res["reason"])
        res = rejected(with_sentence("7 transaction records were retrieved.", ["transaction_records"]))
        check("count of a list value is allowed", res["status"] == "generated", res["reason"])

        # ------------------------------------------------ 4. MISSING semantics
        print("[4] MISSING cannot become a negative, false or failed conclusion")
        negatives = [
            "Prior alerts is false.",
            "Prior alert check failed.",
            "Prior alerts are none.",
            "There are no prior alerts.",
            "Prior alerts were negative.",
            "Prior alert history was rejected.",
            "Prior alert history is suspicious.",
            "Prior alert history is clean.",
            "The customer has zero prior alerts.",
            "The customer has 0 prior alerts.",
        ]
        for text in negatives:
            for where in ("summary_sentences", "key_findings", "analyst_attention"):
                bad = json.loads(json.dumps(good))
                bad[where][-1] = {"text": text, "evidence_keys": ["prior_alerts"]}
                res = rejected(bad)
                check(f"{where}: {text!r} rejected", res["status"] == "error", res["reason"])
        res = rejected(with_sentence("2 prior alerts are recorded.", ["prior_alerts"], at=2))
        check("narrative inventing a value for a MISSING item rejected",
              res["status"] == "error", res["reason"])
        res = rejected(with_sentence("2 prior alerts are recorded.", ["kyb_status"], at=2))
        check("narrative stating a MISSING item under another citation rejected",
              res["status"] == "error" and "does not cite" in res["reason"], res["reason"])
        for text in ("Prior alert history was not available in the collected evidence.",
                     "The prior alert count is missing from the collected evidence.",
                     "Prior alert history is unavailable."):
            res = rejected(with_sentence(text, ["prior_alerts"], at=2))
            check(f"correct MISSING wording accepted: {text!r}", res["status"] == "generated",
                  res["reason"])

        # ------------------------------------------------ 5. no decision
        print("[5] no decision, recommendation, confidence or procedure")
        decisions = [
            "Consider escalating this alert.",
            "We recommend closing the alert.",
            "Confidence in this assessment is high.",
            "The alert can be cleared.",
            "This activity is suspicious.",
            "This looks like fraud.",
            "Outcome: escalate to mlro.",
            *[f"Suggested outcome {o}." for o in outcomes],
        ]
        for text in decisions:
            res = rejected(dict(good, analyst_attention=[{"text": text,
                                                          "evidence_keys": ["country_exposure"]}]))
            check(f"decision language rejected: {text!r}", res["status"] == "error",
                  res["reason"])
            res = rejected(with_sentence(f"Top country exposure is AE. {text}", ["country_exposure"]))
            check(f"decision language rejected in the narrative: {text!r}",
                  res["status"] == "error", res["reason"])
        procedures = [
            "Investigate whether the customer should be escalated.",
            "Check additional transaction history for unusual activity.",
            "Review the AE exposure.",
            "The analyst should look at AE.",
            "AE exposure needs to be verified.",
        ]
        for text in procedures:
            res = rejected(dict(good, analyst_attention=[{"text": text,
                                                          "evidence_keys": ["country_exposure"]}]))
            check(f"procedure rejected in analyst_attention: {text!r}",
                  res["status"] == "error", res["reason"])
        for text in ("AE appears both as country_exposure and in programme_high_risk_list.",
                     "Prior alert history was not available in the collected evidence."):
            keys = ["prior_alerts"] if "Prior" in text else ["country_exposure",
                                                             "programme_high_risk_list"]
            res = rejected(dict(good, analyst_attention=[{"text": text, "evidence_keys": keys}]))
            check(f"observation accepted in analyst_attention: {text!r}",
                  res["status"] == "generated", res["reason"])

        # ------------------------------------------------ 5b. evidence wording is not a decision
        # Found against the real model: kyc_status "pending" is also an outcome
        # name, and match_confidence_band contains "confidence". Reporting the
        # evidence's own words must pass; the same words as a judgement must not.
        print("[5b] the evidence's own words are not mistaken for decision language")
        by_id = {a["alert_id"]: a for a in raw["alerts"]}

        def payload_for(alert_id: str) -> dict:
            al = {k: v for k, v in by_id[alert_id].items() if not k.startswith("_")}
            return build_summary_input(engine, engine.investigate(al))

        def judged(alert_id: str, text: str, keys: list[str], where: str = "key_findings") -> list[str]:
            p = payload_for(alert_id)
            out = grounded_output(p)
            item = {"text": text, "evidence_keys": keys}
            if where == "key_findings":
                out["key_findings"] = [item, *out["key_findings"][1:]]
            else:
                out["summary_sentences"] = [*out["summary_sentences"], item]
            return check_output(out, p, outcomes)

        kyb = next(a["alert_id"] for a in raw["alerts"] if a["rule_id"] == "KYB-FAIL-051"
                   and engine.investigate(a).playbook_id
                   and any(e.value == "pending" for e in engine.investigate(a).evidence))
        san = next(a["alert_id"] for a in raw["alerts"]
                   if any(e.key == "match_confidence_band" and e.status == "FOUND"
                          for e in engine.investigate(a).evidence))
        kyc_key = next(e.key for e in engine.investigate(by_id[kyb]).evidence
                       if e.value == "pending")
        band = next(e.value for e in engine.investigate(by_id[san]).evidence
                    if e.key == "match_confidence_band")
        for alert_id, text, keys, ok in [
            (kyb, f"{kyc_key} is pending.", [kyc_key], True),
            (kyb, f"{kyc_key.replace('_', ' ')} is 'pending'.", [kyc_key], True),
            (kyb, "The outcome should remain pending.", [kyc_key], False),
            (kyb, "Recommend information requested.", [kyc_key], False),
            (san, f"Match confidence band is {band}.", ["match_confidence_band"], True),
            (san, f"The confidence band value is {band}.", ["match_confidence_band"], True),
            (san, "Confidence that this is a true match is high.", ["match_confidence_band"],
             False),
            (san, "The analyst can have confidence in this result.", ["match_confidence_band"],
             False),
        ]:
            probs = judged(alert_id, text, keys)
            check(f"{alert_id}: {text!r} {'accepted' if ok else 'rejected'}",
                  (probs == []) if ok else any("decision" in x or "number" in x for x in probs),
                  str(probs))

        # ------------------------------------------------ 5c. judgement, overstated match, vague missing
        # Found against the real model: "returned a PEP match" for a review flag,
        # "internal inconsistency" between unrelated counts, "notably higher",
        # and a missing line that did not say which item was missing.
        print("[5c] no judgement words, no overstated match, missing items named")
        for text in ("High risk country volume is notably high at 56.",
                     "56 transactions is significantly above normal.",
                     "Country spread is unusually wide at 56.",
                     "56 high risk transactions against 7 records is an internal inconsistency.",
                     "There is a discrepancy between 56 and 7.",
                     "The 7 records are a mismatch with the count of 56.",
                     "Card not present transactions greatly outnumber card present ones at 56.",
                     "Activity of 56 is higher than expected.",
                     "Exposure of 56 is consistent with the stated business."):
            res = rejected(with_finding(text, ["high_risk_country_volume"]))
            check(f"judgement rejected: {text!r}",
                  res["status"] == "error" and "judgement" in res["reason"], res["reason"])
        for text, keys in (("High risk country volume: 56", ["high_risk_country_volume"]),
                           ("Country exposure: AE", ["country_exposure"])):
            res = rejected(with_finding(text, keys))
            check(f"plain fact accepted: {text!r}", res["status"] == "generated", res["reason"])

        rr_key = next(e.key for e in engine.investigate(by_id[san]).evidence
                      if e.field == "review_required")
        for text, ok in [
            ("The screening returned a PEP match.", False),
            ("Screening produced a sanctions match.", False),
            ("This is a confirmed match.", False),
            ("The customer was matched against the list.", False),
            (f"Review required is recorded as true, with a {band} match-confidence band.", True),
            (f"The match confidence band is {band}, and review required is recorded as true.", True),
        ]:
            probs = judged(san, text, ["match_confidence_band", rr_key], where="summary_sentences")
            check(f"{san}: {text!r} {'accepted' if ok else 'rejected'}",
                  (probs == []) if ok else any("match the evidence does not establish" in x
                                               for x in probs), str(probs))

        for text, where, ok in [
            ("Some information was not available in the collected evidence.", "summary_sentences", False),
            ("One item is missing.", "summary_sentences", False),
            ("Prior alert count is not available in the collected evidence.", "summary_sentences", True),
            ("Prior alert count: not available in the collected evidence.", "key_findings", True),
            ("Prior alerts: missing", "key_findings", True),
        ]:
            out = (with_sentence(text, ["prior_alerts"], at=2) if where == "summary_sentences"
                   else with_finding(text, ["prior_alerts"]))
            res = rejected(out)
            check(f"missing item {'named' if ok else 'not named'} ({where}): {text!r}",
                  (res["status"] == "generated") if ok
                  else (res["status"] == "error" and "does not name" in res["reason"]),
                  res["reason"])

        # ------------------------------------------------ 5d. analyst attention is a supported observation
        # Found against the real model: records-vs-count juxtapositions,
        # coincidentally equal numbers, restated single values, and a false
        # "instruction" rejection for a field whose name starts with "Review".
        print("[5d] analyst attention: only supported, typed observations")

        def attention(alert_id: str, item: dict) -> list[str]:
            p = payload_for(alert_id)
            return check_output(dict(grounded_output(p), analyst_attention=[item]), p, outcomes)

        vel = next(a["alert_id"] for a in raw["alerts"]
                   if any(e.key == "transaction_records" and e.status == "FOUND"
                          for e in engine.investigate(a).evidence)
                   and any("baseline" in e.field and e.status == "FOUND"
                           for e in engine.investigate(a).evidence)
                   and engine.investigate(a).playbook_id == engine.routing["TM-VEL-014"])
        vev = {e.key: e for e in engine.investigate(by_id[vel]).evidence}
        base_key = next(k for k, e in vev.items() if "baseline" in e.field and e.status == "FOUND"
                        and e.field.startswith("customer"))
        act_key = next(k for k, e in vev.items() if e.field == "transaction_count")
        n_rec = len(vev["transaction_records"].value)
        same_number = [k for k, e in vev.items() if isinstance(e.value, int) and e.status == "FOUND"]
        for alert_id, item, ok, why in [
            (ALERT_ID, {"kind": "same_value_in_items",
                        "text": "AE appears in both country exposure and programme high risk list.",
                        "evidence_keys": ["country_exposure", "programme_high_risk_list"]},
             True, "shared text value"),
            (ALERT_ID, {"kind": "missing_evidence",
                        "text": "Prior alert count: not available in the collected evidence.",
                        "evidence_keys": ["prior_alerts"]}, True, "missing item"),
            (vel, {"kind": "observed_vs_baseline",
                   "text": f"Transaction activity: {vev[act_key].value}; customer transaction "
                           f"baseline: {vev[base_key].value}.",
                   "evidence_keys": [act_key, base_key]}, True, "count beside its baseline"),
            (vel, {"kind": "observed_vs_baseline",
                   "text": f"Transaction records list contains {n_rec} items while transaction "
                           f"activity is {vev[act_key].value}.",
                   "evidence_keys": ["transaction_records", act_key]}, False,
             "records vs count juxtaposition"),
            (ALERT_ID, {"kind": "same_value_in_items",
                        "text": "High risk country volume: 56 and country spread: 10.",
                        "evidence_keys": ["high_risk_country_volume", "country_spread"]},
             False, "two unrelated numbers"),
            (ALERT_ID, {"kind": "same_value_in_items", "text": "KYB status: complete.",
                        "evidence_keys": ["kyb_status"]}, False, "restated single value"),
            (ALERT_ID, {"kind": "missing_evidence", "text": "KYB status: complete.",
                        "evidence_keys": ["kyb_status"]}, False, "kind not supported by evidence"),
            (ALERT_ID, {"text": "AE appears in both country exposure and programme high risk list.",
                        "evidence_keys": ["country_exposure", "programme_high_risk_list"]},
             True, "untyped item still verified by its evidence"),
            (ALERT_ID, {"text": "KYB status: complete.", "evidence_keys": ["kyb_status"]},
             False, "untyped restatement still rejected"),
        ]:
            probs = attention(alert_id, item)
            check(f"attention {'accepted' if ok else 'rejected'} ({why})",
                  (probs == []) if ok else any("analyst_attention" in x for x in probs), str(probs))
        if len(same_number) >= 2:
            a, b = same_number[:2]
            probs = attention(vel, {"kind": "same_value_in_items",
                                    "text": f"{vev[a].value} and {vev[b].value} are present.",
                                    "evidence_keys": [a, b]})
            check("attention rejected (numbers are never a shared value)",
                  any("analyst_attention" in x for x in probs), str(probs))

        p = payload_for(san)
        out = dict(grounded_output(p), analyst_attention=[
            {"text": "Review required is true.", "evidence_keys": [rr_key]}])
        probs = check_output(out, p, outcomes)
        check("a field named 'review required' is not mistaken for an instruction",
              not any("instruction" in x for x in probs), str(probs))
        out["analyst_attention"] = [{"text": "Review the screening result.", "evidence_keys": [rr_key]}]
        check("an actual 'Review …' instruction is still rejected",
              any("instruction" in x for x in check_output(out, p, outcomes)))

        s = rejected(dict(good, analyst_attention=[
            {"kind": "same_value_in_items",
             "text": "AE appears in both country exposure and programme high risk list.",
             "evidence_keys": ["country_exposure", "programme_high_risk_list"]}]))
        check("attention kind is used for checking, not returned by the API",
              s["status"] == "generated" and all(set(i) == {"text", "evidence_keys"}
                                                 for i in s["analyst_attention"]), str(s))
        check("schema makes the model declare the kind first",
              list(__import__("backend.ai.analyst", fromlist=["x"]).output_schema(["a"])
                   ["properties"]["analyst_attention"]["items"]["properties"])[0] == "kind")

        # ------------------------------------------------ 5e. natural narrative, strictly grounded
        print("[5e] natural sentences accepted; unsupported relationships, values and claims rejected")
        vp = payload_for(vel)
        vfind = [finding(k, vp["evidence"][k]) for k in (act_key, base_key)]
        prog_key = next(k for k, e in vev.items() if e.field.startswith("programme") and "baseline" in e.field)
        A, B, P = vev[act_key].value, vev[base_key].value, vev[prog_key].value
        vtype = f"An alert for {readable(vp['playbook']['category'])} has been raised."

        def narrative(alert_payload: dict, *items, kf=None, att=()) -> list[str]:
            ss = [{"text": t, "evidence_keys": list(k)} for t, *k in items]
            out = {"summary_sentences": ss, "key_findings": kf if kf is not None else vfind,
                   "analyst_attention": list(att)}
            return check_output(out, alert_payload, outcomes)

        obs = (f"The available data shows transaction activity of {A}, with customer and programme "
               f"baselines of {B} and {P} respectively.", act_key, base_key, prog_key)
        enrich = [(k, e) for k, e in vev.items() if e.status == "FOUND" and isinstance(e.value, str)
                  and k not in (act_key,)]
        k1, e1 = enrich[0]
        enrich_ok = (f"The {readable(k1)} is recorded as {readable(e1.value)}.", k1)
        rest = [k for k, e in vev.items() if k not in (act_key, base_key, prog_key, k1)]

        def complete(*middle) -> list[tuple]:
            """The velocity narrative with the remaining items stated plainly."""
            tail = []
            for k in rest:
                e = vev[k]
                if e.status == "MISSING":
                    tail.append((f"{readable(k).capitalize()} is not available in the collected evidence.", k))
                else:
                    tail.append(("The available data also shows " + state(vp["evidence"][k]) + ".", k))
            return [(vtype,), *middle, *tail]

        check("a natural narrative with baselines side by side is accepted",
              narrative(vp, *complete(obs, enrich_ok)) == [],
              str(narrative(vp, *complete(obs, enrich_ok))))
        for text, why, expect in [
            (f"Transaction activity: {A}. Customer transaction baseline: {B}.", "field: value list", "Name: value"),
            (f"Transaction activity of {A} is 7 times the customer baseline of {B}.", "a multiple", "relates numbers"),
            (f"Transaction activity of {A} is above the customer baseline of {B}.", "above", "relates numbers"),
            (f"Transaction activity of {A} is higher than the customer baseline of {B}.", "higher than", "relates numbers"),
            (f"Transaction activity of {A} exceeds the customer baseline of {B}.", "exceeds", "relates numbers"),
            (f"Transaction activity of {A}, compared with a customer baseline of {B}.", "compared with", "relates numbers"),
            (f"Transaction activity of {A} represents a rise from the customer baseline of {B}.", "a rise", "relates numbers"),
            (f"The difference between transaction activity of {A} and the customer baseline of {B} is large.",
             "a difference", "relates numbers"),
            (f"Transaction activity is {A}, and the alert threshold is {B}.", "a threshold", "threshold"),
            (f"The alert was triggered because transaction activity is {A} and the customer baseline is {B}.",
             "a cause", "cause"),
            (f"Transaction activity of {A} indicates heavy use, with a customer baseline of {B}.",
             "an inference", "inference"),
        ]:
            probs = narrative(vp, *complete((text, act_key, base_key), enrich_ok))
            check(f"narrative rejected ({why})", any(expect in x for x in probs), str(probs))

        fp = payload_for("ALRT-0090") if "ALRT-0090" in by_id else None
        if fp:
            fev = fp["evidence"]
            ftype = f"An alert for {readable(fp['playbook']['category'])} has been raised."
            f_fail, f_act = fev["failed_transaction_count"]["value"], fev["transaction_activity"]["value"]
            fkf = [finding("failed_transaction_count", fev["failed_transaction_count"]),
                   finding("transaction_activity", fev["transaction_activity"])]
            others = [k for k in fev if k not in ("failed_transaction_count", "transaction_activity")]
            ftail = [{"text": "The available data also shows " + ", ".join(state(fev[k]) for k in others) + ".",
                      "evidence_keys": others}]

            def fcheck(text: str) -> list[str]:
                ss = [{"text": ftype, "evidence_keys": []},
                      {"text": text, "evidence_keys": ["failed_transaction_count", "transaction_activity"]}, *ftail]
                return check_output({"summary_sentences": ss, "key_findings": fkf, "analyst_attention": []},
                                    fp, outcomes)

            probs = fcheck(f"The available data shows {f_fail} failed transactions and transaction activity of {f_act}.")
            check("two counts side by side, unrelated, accepted", probs == [], str(probs))
            check("'failed transaction count' is not read as also naming 'transaction count'",
                  not any("does not cite" in x for x in probs), str(probs))
            for joiner in ("out of", "of which", "accounting for", "a share of", "representing"):
                text = (f"The available data shows {f_fail} failed transactions {joiner} transaction "
                        f"activity of {f_act}.")
                probs = fcheck(text)
                check(f"unsupported part-of-whole rejected: {joiner!r}",
                      any("relates numbers" in x for x in probs), str(probs))
            probs = fcheck(f"The available data shows {f_fail} failed transactions, or 16% of transaction "
                           f"activity of {f_act}.")
            check("a computed percentage rejected", any("relates numbers" in x or "number not present" in x
                                                         for x in probs), str(probs))

        enrich_drop = next(((k, e) for k, e in enrich if len(e.value.split("_")) >= 3), None)
        if enrich_drop:
            k2, e2 = enrich_drop
            shortened = " ".join(e2.value.split("_")[:-1])
            probs = narrative(vp, *complete(obs, (f"The {readable(k2)} is recorded as {shortened}.", k2)))
            check(f"a value with a word dropped is rejected ({e2.value!r} as {shortened!r})",
                  any("does not state its value" in x for x in probs), str(probs))
        probs = narrative(vp, *complete(obs, (f"The {readable(k1)} is recorded as verified-ish.", k1)))
        check("a reworded value is rejected", any("does not state its value" in x for x in probs), str(probs))
        probs = narrative(vp, *complete((f"The available data shows transaction activity of {A}, with customer "
                                         f"and programme baselines of {B} and {P} respectively, and "
                                         f"{readable(k1)} of {readable(e1.value)}.", act_key, base_key, prog_key)))
        check("a stated item that is not cited is rejected",
              any("does not cite" in x for x in probs), str(probs))
        n_records = len(vev["transaction_records"].value)
        words = {6: "Six", 7: "Seven", 8: "Eight", 9: "Nine", 10: "Ten", 11: "Eleven", 12: "Twelve"}
        if n_records in words and "transaction_records" in rest:
            probs = narrative(vp, (vtype,), obs, enrich_ok,
                              *[t for t in complete()[1:] if t[1] != "transaction_records"],
                              (f"{words[n_records]} transaction records were retrieved.", "transaction_records"))
            check("a number written in words is rejected", any("in words" in x for x in probs), str(probs))
        probs = narrative(vp, *[("An alert has been raised.",), *complete(obs, enrich_ok)[1:]])
        check("the narrative must begin with the alert type",
              any("alert type" in x for x in probs), str(probs))
        probs = narrative(vp, *[(vtype.replace("raised.", f"raised with {A} transactions."),),
                                *complete(obs, enrich_ok)[1:]])
        check("the alert-type sentence cannot state an uncited number",
              any("without citing" in x for x in probs), str(probs))
        check("the category's own words are not judged ('unusual' in the alert type)",
              "unusual" not in vtype or narrative(vp, *complete(obs, enrich_ok)) == [])
        probs = narrative(vp, *complete(obs, enrich_ok), kf=vfind[:1])
        check("fewer than 2 key findings rejected", any("key_findings has" in x for x in probs), str(probs))
        probs = narrative(vp, *complete(obs, enrich_ok), kf=vfind * 3)
        check("more than 4 key findings rejected", any("key_findings has" in x for x in probs), str(probs))
        probs = narrative(vp, *complete(obs, enrich_ok),
                          kf=[{"text": f"Transaction activity: {A}; customer baseline: {B}",
                               "evidence_keys": [act_key, base_key]}, vfind[1]])
        check("a key finding states exactly one item", any("exactly one" in x for x in probs), str(probs))
        probs = narrative(vp, *[(vtype,)] * 9)
        check("more than 8 sentences rejected", any("sentences" in x for x in probs), str(probs))

        # MISSING handled in the narrative
        mp = payload
        msent = [{"text": f"An alert for {readable(mp['playbook']['category'])} has been raised.",
                  "evidence_keys": []}, *sentences[1:2]]
        mixed = msent + [{"text": "Top country exposure is AE, and prior alerts are not available.",
                          "evidence_keys": ["country_exposure", "prior_alerts"]}]
        probs = check_output(dict(good, summary_sentences=mixed), mp, outcomes)
        check("a MISSING item mixed into a FOUND sentence is rejected",
              any("own sentence" in x for x in probs), str(probs))
        probs = check_output(with_finding("Prior alerts: unknown", ["prior_alerts"]), mp, outcomes)
        check("a MISSING key finding must say it is not available",
              any("not available" in x for x in probs), str(probs))

        # a FOUND 0 may be written "no"
        zp = next((payload_for(a["alert_id"]) for a in raw["alerts"]
                   if engine.investigate(a).playbook_id == engine.routing["TM-VEL-014"]
                   and any(e.key == "prior_alerts" and e.value == 0 for e in engine.investigate(a).evidence)), None)
        if zp:
            zgood = grounded_output(zp)
            zs = [x for x in zgood["summary_sentences"]]
            zs[1] = {"text": zs[1]["text"].replace(".", "") + ", and no prior alerts are recorded.",
                     "evidence_keys": zs[1]["evidence_keys"]}
            probs = check_output(dict(zgood, summary_sentences=zs), zp, outcomes)
            check("a count of 0 written as 'no prior alerts' is accepted", probs == [], str(probs))

        # true/false needs its full field name
        kp = payload_for(kyb)
        bool_key = next((k for k, e in kp["evidence"].items() if isinstance(e["value"], bool)), None)
        if bool_key:
            e = kp["evidence"][bool_key]
            short = " ".join(e["field"].split("_")[:-1]) or bool_key
            kgood = grounded_output(kp)
            out = dict(kgood, key_findings=[{"text": f"{short.capitalize()}: {str(e['value']).lower()}",
                                             "evidence_keys": [bool_key]}, *kgood["key_findings"][1:]])
            probs = check_output(out, kp, outcomes)
            check(f"true/false without its full field name rejected ({short!r} for {e['field']!r})",
                  any("full name" in x for x in probs), str(probs))

        # a cited item's value is not a mention of another item
        sp = payload_for(san)
        st_key = next((k for k, ev_ in sp["evidence"].items()
                       if ev_["value"] == "review_required" and k != rr_key), None)
        if st_key:
            sgood = grounded_output(sp)
            out = dict(sgood, key_findings=[{"text": "Screening status: review required",
                                             "evidence_keys": [st_key]}, *sgood["key_findings"][1:]])
            probs = check_output(out, sp, outcomes)
            check("a value 'review required' is not read as naming the item 'review required'",
                  not any("does not cite" in x for x in probs), str(probs))

        # ------------------------------------------------ 5f. wording polish, before the checks
        print("[5f] wording polish: spelling, casing and agreement only; still checked as polished")
        from backend.ai.analyst import polish, _polish_text, _polish_finding  # noqa: E402
        for raw_text, want in [
            ("1 prior alerts are recorded.", "1 prior alert is recorded."),
            ("11 transaction records were retrieved, and 1 prior alerts are recorded.",
             "11 transaction records were retrieved, and 1 prior alert is recorded."),
            ("1 previous screening reviews are recorded.", "1 previous screening review is recorded."),
            ("2 prior alerts are recorded.", "2 prior alerts are recorded."),
            ("1 counterparties are recorded.", "1 counterparties are recorded."),  # left alone, never mangled
            ("The screening type is pep and the list type is pep domestic.",
             "The screening type is PEP and the list type is PEP domestic."),
            ("9 failed topups and topup activity of 8.", "9 failed top-ups and top-up activity of 8."),
            ("the kyb and kyc statuses are recorded.", "The KYB and KYC statuses are recorded."),
        ]:
            check(f"polish: {raw_text!r}", _polish_text(raw_text) == want, repr(_polish_text(raw_text)))
        for raw_text, want in [
            ("Review required: review required is recorded as true", "Review required: true"),
            ("KYB status: KYB status is recorded as complete.", "KYB status: complete"),
            ("Screening status: is review required", "Screening status: review required"),
            ("Screening type: pep", "Screening type: PEP"),
            ("topup activity: 8", "Top-up activity: 8"),
            ("Prior alerts: not available", "Prior alerts: not available"),
            ("Failed transaction count: 25", "Failed transactions: 25"),
            ("Distinct merchant category count: 3", "Distinct merchant categories: 3"),
            ("Prior alert count: 2", "Prior alerts: 2"),
            ("Prior alert count: not available", "Prior alert count: not available"),  # only a number
            ("Failed transactions: 25 failed transactions", "Failed transactions: 25"),
            ("Failed top-ups: 9 failed topups", "Failed top-ups: 9"),
            ("Prior alerts: 2 earlier alerts", "Prior alerts: 2 earlier alerts"),  # not a repeat: kept
        ]:
            check(f"finding polish: {raw_text!r}", _polish_finding(raw_text) == want, repr(_polish_finding(raw_text)))
        digits = lambda t: re.findall(r"\d+(?:\.\d+)?", t)  # noqa: E731
        sample = ["1 prior alerts are recorded, 11 records, 0.55 share and 2352 events.",
                  "Review required: review required is recorded as true"]
        check("polish never changes a number",
              all(digits(_polish_text(t)) == digits(t) and digits(_polish_finding(t)) == digits(t) for t in sample))

        # The polished answer is what is checked, and what is returned.
        one_p = next((payload_for(a["alert_id"]) for a in raw["alerts"]
                      if any(e.key == "prior_alerts" and e.value == 1 for e in engine.investigate(a).evidence)), None)
        if one_p:
            g = grounded_output(one_p)
            g["summary_sentences"].append({"text": "1 prior alerts are recorded.", "evidence_keys": ["prior_alerts"]})
            g["key_findings"][0] = {"text": f"{g['key_findings'][0]['text'].split(':')[0]}: "
                                            f"{g['key_findings'][0]['text'].split(':')[0]} is recorded as "
                                            f"{g['key_findings'][0]['text'].split(': ', 1)[1]}",
                                    "evidence_keys": g["key_findings"][0]["evidence_keys"]}
            s1 = AIAnalyst(FakeClient(returns(g)), TEST_MODEL, outcomes).summarize(one_p)
            check("a polished answer is accepted and returned polished",
                  s1["status"] == "generated" and "1 prior alert is recorded." in s1["summary"]
                  and " is recorded as " not in s1["key_findings"][0]["text"], str(s1.get("reason")) + str(s1["key_findings"][:1]))
        for unsafe in (f"Transaction activity of {A} is above the customer baseline of {B}.",
                       f"The available data shows {A} topups, which is concerning."):
            g = json.loads(json.dumps(grounded_output(vp)))
            g["summary_sentences"][1] = {"text": unsafe, "evidence_keys": [act_key, base_key]}
            s1 = AIAnalyst(FakeClient(returns(g)), TEST_MODEL, outcomes).summarize(vp)
            check(f"polish does not make an unsafe sentence pass: {unsafe!r}", s1["status"] == "error", str(s1.get("reason")))
        tp = next((payload_for(a["alert_id"]) for a in raw["alerts"]
                   if engine.investigate(a).playbook_id == engine.routing["TM-TOP-021"]
                   and any(e.key == "topup_activity" and e.status == "FOUND" for e in engine.investigate(a).evidence)), None)
        if tp:
            g = grounded_output(tp)
            hidden = dict(g, summary_sentences=[*g["summary_sentences"],
                          {"text": f"Top-up activity of {tp['evidence']['topup_activity']['value']} is recorded.",
                           "evidence_keys": ["kyb_status"] if "kyb_status" in tp["evidence"] else []}])
            probs = check_output(polish(hidden), tp, outcomes)
            check("'top-up activity' still counts as naming the item topup activity (must be cited)",
                  any("does not cite" in x and "topup_activity" in x for x in probs), str(probs))

        # ------------------------------------------------ 6. failure handling
        print("[6] failures give a controlled state; investigation unaffected")
        a = AIAnalyst.from_env(outcomes, environ={MODEL_ENV: "x"})
        check("no API key -> unavailable, no client built",
              not a.available and API_KEY_ENV in a.unavailable_reason)
        a = AIAnalyst.from_env(outcomes, environ={API_KEY_ENV: "  ", MODEL_ENV: "x"})
        check("blank API key -> unavailable", not a.available)
        a = AIAnalyst.from_env(outcomes, environ={API_KEY_ENV: "k"})
        check("no model configured -> unavailable", not a.available
              and MODEL_ENV in a.unavailable_reason)
        app.state.ai_analyst = AIAnalyst.from_env(outcomes, environ={})
        r = client.post("/investigate/summary", json=body)
        s = r.json()["ai_summary"]
        check("endpoint with no key: 200 + unavailable + no content",
              r.status_code == 200 and s["status"] == "unavailable" and s["summary"] is None
              and s["generated_by"] is None and s["evidence_basis"] == []
              and "not configured" in s["reason"], str(s))

        import openai
        import httpx
        req = httpx.Request("POST", "https://api.openai.com/v1/responses")
        for exc in (openai.APITimeoutError(request=req),
                    openai.APIConnectionError(request=req),
                    openai.AuthenticationError("bad key", response=httpx.Response(401, request=req),
                                               body=None),
                    openai.RateLimitError("slow down", response=httpx.Response(429, request=req),
                                          body=None),
                    RuntimeError("boom")):
            use(raises(exc))
            r = client.post("/investigate/summary", json=body)
            s = r.json()["ai_summary"]
            check(f"{type(exc).__name__} -> 200 + error",
                  r.status_code == 200 and s["status"] == "error"
                  and type(exc).__name__ in s["reason"] and s["summary"] is None, str(s))

        for label, out, status in [
            ("not JSON", "The customer is in DE.", "completed"),
            ("empty output", "", "completed"),
            ("JSON array", "[]", "completed"),
            ("missing fields", {"summary": "x"}, "completed"),
            ("extra field", {**good, "confidence": "high"}, "completed"),
            ("extra outcome field", {**good, "recommended_outcome": outcomes[0]}, "completed"),
            ("item wrong shape", {**good, "key_findings": ["AE"]}, "completed"),
            ("empty summary", {**good, "summary_sentences": []}, "completed"),
            ("old flat summary field", {**good, "summary": "x"}, "completed"),
            ("incomplete response", good, "incomplete"),
        ]:
            use(returns(out, status))
            r = client.post("/investigate/summary", json=body)
            s = r.json()["ai_summary"]
            check(f"malformed ({label}) -> 200 + error, nothing shown",
                  r.status_code == 200 and s["status"] == "error" and s["summary"] is None
                  and s["key_findings"] == [], str(s.get("reason")))

        fake = use(raises(RuntimeError("never called")))
        r = client.post("/investigate/summary",
                        json={"alert": {**body["alert"], "rule_id": "NOPE-000"}})
        check("unknown rule_id -> 400, model never called",
              r.status_code == 400 and fake.responses.calls == [])

        # ------------------------------------------------ 7. /investigate unchanged
        print("[7] deterministic investigation unchanged")
        use(raises(RuntimeError("AI down")))
        inv_after = client.post("/investigate", json=body)
        check("POST /investigate still 200 while the AI fails", inv_after.status_code == 200)
        check("POST /investigate output identical before and after AI calls",
              inv_after.json() == inv_before)
        check("POST /investigate response has no AI field",
              not any("ai_summary" in k or k == "summary" for k in inv_before))
        check("POST /investigate never calls the model",
              app.state.ai_analyst.client.responses.calls == [])
        check("summary endpoint investigation matches /investigate",
              build_summary_input(engine, engine.investigate(body["alert"])) == payload)

        app.state.ai_analyst = original_analyst

    # ---------------------------------------------------- 8. static guarantees
    print("[8] no secrets, no hardcoded facts, one model call site")
    src_files = [p for p in PROJECT_ROOT.rglob("*")
                 if p.is_file() and "node_modules" not in p.parts and "__pycache__" not in p.parts
                 and p.name != ".env" and p.suffix in {".py", ".ts", ".tsx", ".md", ".txt",
                                                        ".json", ".yaml", ".example", ".mjs"}]
    key_like = re.compile(r"sk-(proj-)?[A-Za-z0-9_\-]{20,}")
    leaks = [str(p.relative_to(PROJECT_ROOT)) for p in src_files
             if key_like.search(p.read_text(encoding="utf-8", errors="ignore"))]
    check("no API key in any project file", not leaks, str(leaks))
    check(".env is gitignored",
          ".env" in (PROJECT_ROOT / ".gitignore").read_text().splitlines())
    ex = (PROJECT_ROOT / ".env.example").read_text()
    check(".env.example has an empty key and names the model",
          re.search(r"^OPENAI_API_KEY=\s*$", ex, re.M) is not None
          and re.search(r"^OPENAI_MODEL=\S+", ex, re.M) is not None)

    analyst_src = (PROJECT_ROOT / "backend" / "ai" / "analyst.py").read_text(encoding="utf-8")
    pbs = yaml.safe_load((KB_DIR / "playbooks.yaml").read_text())["playbooks"]
    facts = {*pbs, *(pb["category"] for pb in pbs.values()),
             *(r for pb in pbs.values() for r in pb.get("rule_ids", [])),
             *(ev["key"] for pb in pbs.values() for ev in pb["required_evidence"]),
             *(a["alert_id"] for a in raw["alerts"]), *(a["customer_ref"] for a in raw["alerts"])}
    hard = sorted(f for f in facts if re.search(rf"\b{re.escape(f)}\b", analyst_src))
    check("analyst.py names no alert, rule, category, playbook or evidence key", not hard,
          str(hard))
    check("model name is not hardcoded in application code",
          not any("gpt-" in p.read_text(encoding="utf-8")
                  for p in (PROJECT_ROOT / "backend").rglob("*.py")
                  if "tests" not in p.parts))
    importers = sorted(str(p.relative_to(PROJECT_ROOT)) for p in (PROJECT_ROOT / "backend").rglob("*.py")
                       if "tests" not in p.parts
                       and re.search(r"^\s*(from openai|import openai)", p.read_text(encoding="utf-8"),
                                     re.M))
    check("openai is imported only by the AI analyst",
          importers == [str(Path("backend/ai/analyst.py"))], str(importers))
    check("canonical dataset unchanged",
          hashlib.md5(DATASET_PATH.read_bytes()).hexdigest() == dataset_md5)

    # check_output is usable on its own (used by the scratch demo script)
    check("check_output accepts the grounded answer",
          check_output(good, payload, outcomes) == [])

    print()
    for f in failed:
        print("  FAIL", f)
    print(f"\n{len(passed)} passed, {len(failed)} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
