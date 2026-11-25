"""
Cache of accepted AI summaries (alert and cluster).

Proves, with a counting fake model client and no network:
  - a repeated request makes one model call; the second is served from cache
  - the cache survives an app restart
  - a changed model, prompt, or input (including changed evidence) makes a new call
  - regenerate=true always makes a new call and replaces the entry only when the
    new answer is accepted
  - unavailable, failed and rejected results are never cached
  - a cached answer is re-checked against the current grounding checks and
    regenerated if it no longer passes
  - the cache lives in its own file; dataset.json and routing.json are untouched

Uses temporary files via UC3_AI_CACHE_PATH, UC3_ROUTING_PATH and, for the
changed-evidence case, UC3_DATASET_PATH. Exit code 0 on success.
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

TMP = Path(tempfile.mkdtemp(prefix="uc3-ai-cache-"))
CACHE = TMP / "ai_cache.json"
os.environ["UC3_AI_CACHE_PATH"] = str(CACHE)
os.environ["UC3_ROUTING_PATH"] = str(TMP / "routing.json")

import yaml  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import backend.api.routes as routes  # noqa: E402
from backend.ai import AIAnalyst  # noqa: E402
from backend.ai.cache import fingerprint  # noqa: E402
from backend.api import app  # noqa: E402
from backend.paths import DATASET_PATH, KB_DIR, PROJECT_ROOT  # noqa: E402

passed: list[str] = []
failed: list[str] = []
MODEL = "test-model-from-env"
ALERT = "ALRT-0047"


def check(name: str, cond: bool, detail: str = "") -> None:
    (passed if cond else failed).append(f"{name}{(' — ' + detail) if detail else ''}")


class Fake:
    """Counts calls; answers from the payload it receives (so every answer is grounded)."""

    def __init__(self, tag: str = "v1", mode: str = "good"):
        self.calls = 0
        self.tag, self.mode = tag, mode
        self.responses = self

    def create(self, **kw):
        self.calls += 1
        if self.mode == "raise":
            raise RuntimeError("down")
        p = json.loads(kw["input"])
        out = alert_answer(p, self.tag) if "evidence" in p else cluster_answer(p, self.tag)
        if self.mode == "rejected":
            if "evidence" in p:
                out["summary_sentences"][0]["text"] += " We recommend escalating this."
            else:
                out["summary"] = "We recommend escalating this."
        return SimpleNamespace(status="completed", output_text=json.dumps(out))


def alert_answer(p: dict, tag: str) -> dict:
    # The narrative shape: the alert type (carrying the tag, so a test can tell
    # answers apart), then the MISSING items; two key findings state FOUND values.
    ev = p["evidence"]
    found = [k for k, v in ev.items() if v["status"] == "FOUND"
             and isinstance(v["value"], (str, int, float)) and not isinstance(v["value"], bool)]
    missing = [k for k, v in ev.items() if v["status"] == "MISSING"]
    sentences = [{"text": f"Summary {tag} - an alert for {p['playbook']['category'].replace('_', ' ')} "
                          f"has been raised.", "evidence_keys": []}]
    if missing:
        sentences.append({"text": f"{', '.join(k.replace('_', ' ') for k in missing)} not available in "
                                  f"the collected evidence.", "evidence_keys": missing})
    value = lambda k: ev[k]["value"].replace("_", " ") if isinstance(ev[k]["value"], str) else ev[k]["value"]
    return {
        "summary_sentences": sentences if len(sentences) > 1 else [*sentences, *[
            {"text": f"The {ev[k]['field'].replace('_', ' ')} is {value(k)}.", "evidence_keys": [k]}
            for k in found[:1]]],
        "key_findings": [{"text": f"{ev[k]['field'].replace('_', ' ').capitalize()}: {value(k)}",
                          "evidence_keys": [k]} for k in found[:2]],
        "analyst_attention": [{"kind": "missing_evidence", "text": f"{missing[0]}: not available in the collected evidence.",
                               "evidence_keys": [missing[0]]}] if missing else [],
    }


def cluster_answer(p: dict, tag: str) -> dict:
    c = p["cluster"]
    return {
        "summary": f"Summary {tag}: {c['label']} contains {c['alert_count']} alerts.",
        "summary_fields": ["cluster.label", "cluster.alert_count"],
        "criteria_met": [{"text": f"Alerts: {p['criteria']['min_alerts']['actual']} against a minimum of "
                                  f"{p['criteria']['min_alerts']['threshold']} (met).", "fields": ["criteria.min_alerts"]}],
        "characteristics": [{"text": f"Programme: {', '.join(c['programme_refs'])}.", "fields": ["cluster.programme_refs"]}],
    }


def main() -> int:
    md5 = lambda p: hashlib.md5(p.read_bytes()).hexdigest() if p.exists() else None  # noqa: E731
    dataset_before = md5(DATASET_PATH)
    project_routing_before = md5(PROJECT_ROOT / "data" / "routing.json")
    # The application keeps a real, persistent cache at data/ai_cache.json. The
    # suite works on its own temporary cache; it must leave the project's
    # cache exactly as it found it, whether or not one exists.
    project_cache = PROJECT_ROOT / "data" / "ai_cache.json"
    project_cache_before = md5(project_cache)
    outcomes = yaml.safe_load((KB_DIR / "config.yaml").read_text())["investigation_outcomes"]
    raw = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    alert = {k: v for k, v in next(a for a in raw["alerts"] if a["alert_id"] == ALERT).items() if not k.startswith("_")}
    other = {k: v for k, v in next(a for a in raw["alerts"] if a["alert_id"] == "ALRT-0001").items() if not k.startswith("_")}

    def use(fake: Fake, model: str = MODEL) -> Fake:
        app.state.ai_analyst = AIAnalyst(fake, model, outcomes)
        return fake

    def summary(c, a=alert, **q):
        r = c.post("/investigate/summary" + ("?regenerate=true" if q.get("regenerate") else ""), json={"alert": a})
        return r.json()["ai_summary"]

    print("=" * 78)
    print("AI SUMMARY CACHE")
    print("=" * 78)

    with TestClient(app) as c:
        clusters = c.get("/clusters").json()["clusters"]
        members = clusters[0]["alert_ids"]

        def csummary(ids=members, **q):
            r = c.post("/clusters/summary" + ("?regenerate=true" if q.get("regenerate") else ""),
                       json={"alert_ids": ids})
            return r.json()["ai_summary"]

        print("\n[1] alert summary: one call for repeated requests")
        f = use(Fake("v1"))
        s1, s2 = summary(c), summary(c)
        check("first request calls the model once", f.calls == 1, str(f.calls))
        check("second request makes no model call", f.calls == 1)
        check("first is fresh, second is from cache",
              s1["cached"] is False and s2["cached"] is True and s1["status"] == s2["status"] == "generated")
        check("cached summary identical, with the original generation time",
              s2["summary"] == s1["summary"] and s2["generated_at"] == s1["generated_at"] and s1["generated_at"])
        check("evidence basis still attached to a cached summary", s2["evidence_basis"] == s1["evidence_basis"]
              and len(s2["evidence_basis"]) > 0)
        check("cache file written in its own location", CACHE.exists() and len(json.loads(CACHE.read_text())) == 1)

        print("[2] regenerate forces a new call and replaces an accepted answer")
        f = use(Fake("v2"))
        s3 = summary(c, regenerate=True)
        check("regenerate calls the model even with a cache entry", f.calls == 1 and s3["cached"] is False)
        check("regenerated answer is the new one", s3["summary"].startswith("Summary v2"))
        s4 = summary(c)
        check("next normal request serves the replaced answer with no call",
              f.calls == 1 and s4["cached"] is True and s4["summary"].startswith("Summary v2"))

        print("[3] failures and rejections are never cached")
        for mode in ("rejected", "raise"):
            f = use(Fake("v3", mode))
            s = summary(c, regenerate=True)
            check(f"regenerate with a {mode} answer returns error and calls once",
                  f.calls == 1 and s["status"] == "error" and s["cached"] is False)
            f = use(Fake("v9"))
            s = summary(c)
            check(f"the {mode} answer did not replace the cached one",
                  f.calls == 0 and s["cached"] is True and s["summary"].startswith("Summary v2"))
        app.state.ai_analyst = AIAnalyst.from_env(outcomes, environ={})
        s = summary(c, other)
        check("unavailable (no key) is returned and not cached",
              s["status"] == "unavailable" and fingerprint is not None
              and len(json.loads(CACHE.read_text())) == 1)
        f = use(Fake("v1"))
        summary(c, other)
        check("after an unavailable result, the next request calls the model", f.calls == 1)

        print("[4] changed model, prompt or input means a new call")
        f = use(Fake("m2"), model="another-model")
        s = summary(c)
        check("changed OPENAI_MODEL: new call", f.calls == 1 and s["cached"] is False)
        f = use(Fake("p2"))
        original = routes.INSTRUCTIONS
        routes.INSTRUCTIONS = original + "\n(prompt edit)"
        try:
            s = summary(c)
            check("changed prompt: new call", f.calls == 1 and s["cached"] is False)
        finally:
            routes.INSTRUCTIONS = original
        f = use(Fake("x"))
        summary(c)
        check("original prompt again: served from cache", f.calls == 0)
        summary(c, other)
        check("different alert (different input): its own entry, already cached above", f.calls == 0)

        print("[5] cached answers are re-checked against the current checks")
        original_check = routes.check_output
        routes.check_output = lambda out, payload, names: ["stricter rule"]
        try:
            f = use(Fake("s1"))
            summary(c)
            check("an entry failing today's checks is not served; the model is called", f.calls == 1)
        finally:
            routes.check_output = original_check

        print("[6] no key configured, but an accepted answer is cached: served without a call")
        app.state.ai_analyst = AIAnalyst.from_env(outcomes, environ={"OPENAI_MODEL": MODEL})
        s = summary(c)
        check("served from cache with no client", s["status"] == "generated" and s["cached"] is True)

        print("[7] cluster summary: same behaviour")
        f = use(Fake("c1"))
        a, b = csummary(), csummary()
        check("repeated cluster request: one call", f.calls == 1 and a["cached"] is False and b["cached"] is True)
        check("cached cluster summary identical", a["summary"] == b["summary"] and a["generated_at"] == b["generated_at"])
        f = use(Fake("c2"))
        r = csummary(regenerate=True)
        check("cluster regenerate: new call, new answer", f.calls == 1 and r["summary"].startswith("Summary c2"))
        check("then served from cache", csummary()["summary"].startswith("Summary c2") and f.calls == 1)
        f = use(Fake("c3", "rejected"))
        csummary(regenerate=True)
        check("rejected cluster answer not cached", csummary()["summary"].startswith("Summary c2"))
        f = use(Fake("c4"))
        csummary(clusters[1]["alert_ids"])
        check("a different cluster (different input) calls the model", f.calls == 1)

    print("[8] the cache survives a restart")
    with TestClient(app) as c2:
        f = use(Fake("after-restart"))
        s = c2.post("/investigate/summary", json={"alert": alert}).json()["ai_summary"]
        cs = c2.post("/clusters/summary", json={"alert_ids": members}).json()["ai_summary"]
        # The latest accepted answer for this alert is "s1": section [5] regenerated it.
        check("alert summary served from cache after restart, no call",
              f.calls == 0 and s["cached"] is True and s["summary"].startswith("Summary s1"),
              f"calls={f.calls} cached={s['cached']} summary={s['summary']!r}")
        check("cluster summary served from cache after restart, no call",
              f.calls == 0 and cs["cached"] is True and cs["summary"].startswith("Summary c2"))

    print("[9] changed evidence means a new call")
    copy_dir = TMP / "data-copy"
    copy_dir.mkdir()
    shutil.copy(DATASET_PATH, copy_dir / "dataset.json")
    d = json.loads((copy_dir / "dataset.json").read_text(encoding="utf-8"))
    d["customers"][alert["customer_ref"]]["industry_segment"] = "changed_segment"
    (copy_dir / "dataset.json").write_text(json.dumps(d, indent=1), encoding="utf-8")
    os.environ["UC3_DATASET_PATH"] = str(copy_dir / "dataset.json")
    try:
        with TestClient(app) as c3:  # same cache file, dataset with one changed evidence value
            f = use(Fake("ev2"))
            s = c3.post("/investigate/summary", json={"alert": alert}).json()["ai_summary"]
            check("changed evidence value: new call, fresh summary", f.calls == 1 and s["cached"] is False)
    finally:
        os.environ.pop("UC3_DATASET_PATH", None)

    print("[10] stores are separate")
    check("dataset.json unchanged", md5(DATASET_PATH) == dataset_before)
    check("project routing.json unchanged", md5(PROJECT_ROOT / "data" / "routing.json") == project_routing_before)
    check("the project's AI cache is untouched (not created, changed or removed); the suite used its own",
          md5(project_cache) == project_cache_before and CACHE.resolve() != project_cache.resolve(),
          f"before={project_cache_before} after={md5(project_cache)}")
    entries = json.loads(CACHE.read_text())
    check("every cached entry is an accepted answer with model and prompt version",
          all(e["result"]["status"] == "generated" and e["model"] and e["prompt_version"] for e in entries.values()))

    print()
    for x in failed:
        print("  FAIL", x)
    print(f"\n{len(passed)} passed, {len(failed)} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
