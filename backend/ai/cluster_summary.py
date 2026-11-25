"""
AI cluster summary.

    deterministic clustering -> validated cluster result -> build_cluster_input()
        -> model (the same OpenAI client and OPENAI_MODEL as the alert analyst)
        -> check_cluster_output() -> AI cluster summary -> analyst

The model explains why the deterministic cluster engine grouped these alerts.
It does not decide membership, recompute thresholds, investigate alerts, judge
risk, or recommend routing, an outcome or any decision. Its answer is accepted
only if every statement cites cluster-data fields, every number appears in the
cluster data, and no decision, suspicion, routing or instruction language is
used; otherwise the caller gets status "error" and no summary.

This module does not import the OpenAI SDK: it uses the client the alert
analyst already holds (AIAnalyst.client), so there is one model configuration.
Nothing here names a cluster type, rule or alert; all of it comes from the
cluster at request time.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from backend.ai.analyst import (
    STATUS_ERROR,
    STATUS_GENERATED,
    STATUS_UNAVAILABLE,
    AIAnalyst,
    _DECISION,
    _JUDGEMENT,
    _NUMBER,
    _PROCEDURE,
    _as_number,
)

log = logging.getLogger("uc3.ai.cluster")

CLUSTER_INSTRUCTIONS = """\
You write an internal, AI-generated explanation of an alert cluster for a human
compliance analyst.

The user message is the result of a deterministic cluster engine that has already
grouped these alerts using configured thresholds. It is your only source of facts.
You explain why the system considers these alerts a cluster. You do not decide
whether they belong together, and you do not advise.

FACTS
1. Use only values in the message. Do not add facts, numbers, dates, amounts,
   customer behaviour or conclusions. Do not compute new figures (no percentages,
   averages, differences or per-customer counts).
2. Report thresholds and actual values exactly as given in `criteria`, and say
   whether each was met only as `criteria` states it.
3. You may state `cluster.time_span_hours` and the time-window threshold, because
   they are computed values. State no other time period.
4. `members` lists the member alerts only. You have no evidence about them; say
   nothing about what any alert or customer did.

BOUNDARIES
5. A cluster is a grouping. It establishes a pattern, never wrongdoing. Do not
   mention or imply suspicion, wrongdoing, fraud, laundering, risk or concern, and
   do not characterise the customers.
6. Obey every rule in `guidance.boundaries`. `guidance.purpose` explains what this
   kind of cluster is for; use it only to describe the pattern, and leave out any
   part of it about where alerts go next.
7. No routing and no decisions: do not mention routing, sending, referring,
   assigning or escalating the cluster or its alerts, do not name any team or
   person it should go to, and do not suggest an outcome. Do not use the words
   recommend, escalate, suspicious or confidence.
8. No evaluative words (notably, significantly, unusually, concerning and the like)
   and no instructions: never start a statement with a verb such as review, check,
   investigate or consider, and never use should, must or need to.

READABILITY
9. Use the cluster's label and plain English. Never write a field name or path
   (such as alert_count, time_window_hours or cluster.label) in any text: field
   names go only in the citation lists. Never write snake_case values or list
   syntax such as brackets and quotes. Copy identifiers such as rule IDs, programme
   and alert IDs exactly, separated by commas.

OUTPUT
10. summary: 2 to 3 plain sentences saying what the cluster contains and why the
    deterministic system grouped it.
11. criteria_met: one statement per entry in `criteria`, in these forms:
    - rule IDs: "Upstream rules: <rule IDs> (all match the configured rules; met)."
    - minimum alerts: "Alerts: <actual> against a minimum of <threshold> (met)."
    - minimum customers: "Distinct customers: <actual> against a minimum of <threshold> (met)."
    - time window: "Time span: <actual span> hours, within the configured window of
      <threshold> hours (met)."
12. characteristics: 1 to 3 short factual statements about the cluster, such as its
    programme(s), upstream rule(s), member count or span. No restatement of the
    criteria.
13. Every statement cites the input fields it relies on, using only the allowed field
    names.
"""

# Field paths the model may cite.
FIELDS = (
    "cluster.label", "cluster.cluster_type", "cluster.cluster_category", "cluster.rule_ids",
    "cluster.programme_refs", "cluster.alert_count", "cluster.customer_count",
    "cluster.time_span_hours", "cluster.member_alert_ids",
    "criteria.rule_ids", "criteria.min_alerts", "criteria.min_customers",
    "criteria.time_window_hours", "grouping", "members", "guidance.purpose", "guidance.boundaries",
)
_LISTS = ("criteria_met", "characteristics")
_OUT = ("summary", "summary_fields", *_LISTS)

_SUSPICION = re.compile(
    r"\b(suspicion|suspect\w*|wrongdoing|illicit|launder\w*|criminal|money laundering|risky|"
    r"high[- ]risk customers?|concern\w*)\b", re.IGNORECASE)
# A screening result overstated as a match. Cluster data carries no screening
# result, so any such claim is unsupported. Unlike the alert check, a bare
# "matched" is allowed here: alerts matching the configured rules is exactly
# what a cluster explanation describes.
_SCREENING_MATCH = re.compile(
    r"\b(confirmed|positive|true|definite|actual)\s+(\w+\s+)?match(es)?\b|"
    r"\b(pep|sanctions?)\s+match(es|ed)?\b|"
    r"\b(returned|produced|found|identified)\s+an?\s+(\w+\s+)?match\b",
    re.IGNORECASE)
# A duration stated in the text. Only the computed span and the configured time
# window exist as durations, both in hours.
_DURATION = re.compile(
    r"(\d+(?:\.\d+)?)\s*-?\s*(hours?|hrs?|h|minutes?|mins?|days?|weeks?|months?|years?)\b",
    re.IGNORECASE)
_ROUTING = re.compile(
    r"\b(rout(e|ed|es|ing)|assign\w*|refer(s|red|ral)?|hand(ed|s)? (off|over)|send|sent|"
    r"forward\w*)\b", re.IGNORECASE)


def build_cluster_input(cluster: dict, match_on: dict, members: list[dict], guidance: dict) -> dict:
    """
    The exact message sent to the model, from the deterministic cluster result,
    the cluster type's configured match rules, the member alert records, and the
    KB cluster purpose and boundaries. analyst_guidance (which routes) and
    interpretation_guidance (possible explanations) are deliberately left out.
    """
    th = cluster["thresholds"]
    required = list(match_on.get("rule_ids", []))
    criteria: dict[str, Any] = {
        "rule_ids": {"required": required, "actual": cluster["rule_ids"],
                     "met": set(cluster["rule_ids"]) <= set(required) if required else True},
    }
    if "min_alerts" in th:
        criteria["min_alerts"] = {"threshold": th["min_alerts"], "actual": len(cluster["alert_ids"]),
                                  "met": len(cluster["alert_ids"]) >= th["min_alerts"]}
    if "min_customers" in th:
        criteria["min_customers"] = {"threshold": th["min_customers"], "actual": len(cluster["customer_refs"]),
                                     "met": len(cluster["customer_refs"]) >= th["min_customers"]}
    if "time_window_hours" in th:
        criteria["time_window_hours"] = {"threshold": th["time_window_hours"],
                                         "actual_span": cluster["time_span_hours"],
                                         "met": cluster["time_span_hours"] <= th["time_window_hours"]}
    return {
        "cluster": {
            "cluster_type": cluster["cluster_type"], "label": cluster["label"],
            "cluster_category": cluster["cluster_category"], "rule_ids": cluster["rule_ids"],
            "programme_refs": cluster["programme_refs"], "alert_count": len(cluster["alert_ids"]),
            "customer_count": len(cluster["customer_refs"]),
            "time_span_hours": cluster["time_span_hours"], "member_alert_ids": cluster["alert_ids"],
        },
        "criteria": criteria,
        "grouping": {"group_by": list(match_on.get("group_by", [])),
                     "same_programme": bool(match_on.get("same_programme", False))},
        "members": [{k: m.get(k) for k in ("alert_id", "rule_id", "customer_ref", "programme_ref", "created_at")}
                    for m in members],
        "guidance": {"purpose": str(guidance.get("purpose", "")).strip(),
                     "boundaries": list(guidance.get("boundaries", []))},
    }


def cluster_output_schema() -> dict:
    fields = {"type": "array", "items": {"type": "string", "enum": list(FIELDS)}}
    item = {"type": "object", "additionalProperties": False, "required": ["text", "fields"],
            "properties": {"text": {"type": "string"}, "fields": fields}}
    return {"type": "object", "additionalProperties": False, "required": list(_OUT),
            "properties": {"summary": {"type": "string"}, "summary_fields": fields,
                           **{k: {"type": "array", "items": item} for k in _LISTS}}}


def _numbers(v: Any, out: set[float]) -> None:
    if isinstance(v, bool):
        return
    if isinstance(v, (int, float)):
        out.add(float(v))
    elif isinstance(v, str):
        for m in _NUMBER.finditer(v):
            out.add(_as_number(m.group()))
    elif isinstance(v, list):
        out.add(float(len(v)))
        for x in v:
            _numbers(x, out)
    elif isinstance(v, dict):
        for x in v.values():
            _numbers(x, out)


def _vocabulary(payload: dict) -> re.Pattern:
    """The cluster data's own words (labels, rule and alert IDs, snake_case values
    and their spaced forms). Guidance text is not included, so quoting a KB
    sentence cannot carry routing or decision wording past the checks."""
    terms: set[str] = set()

    def walk(v: Any) -> None:
        if isinstance(v, str) and v.strip():
            terms.update({v, v.replace("_", " ")})
        elif isinstance(v, list):
            for x in v:
                walk(x)
        elif isinstance(v, dict):
            for x in v.values():
                walk(x)

    for k in ("cluster", "criteria", "grouping", "members"):
        walk(payload[k])
    alts = "|".join(re.escape(t) for t in sorted(terms, key=len, reverse=True)) or r"(?!x)x"
    return re.compile(r"(?<!\w)(" + alts + r")(?!\w)", re.IGNORECASE)


def check_cluster_output(out: Any, payload: dict, destination_labels: list[str],
                         outcome_names: list[str]) -> list[str]:
    """Every reason the model's cluster explanation must be rejected. Empty = accepted."""
    if not isinstance(out, dict) or set(out) != set(_OUT):
        return ["output is not the cluster summary object"]
    problems: list[str] = []
    if not isinstance(out["summary"], str) or not out["summary"].strip():
        problems.append("summary is empty")
    statements = [("summary", out["summary"], out["summary_fields"])]
    for name in _LISTS:
        if not isinstance(out[name], list):
            return [f"{name} is not a list"]
        for i, it in enumerate(out[name]):
            if not (isinstance(it, dict) and set(it) == {"text", "fields"} and isinstance(it["text"], str)
                    and it["text"].strip() and isinstance(it["fields"], list)):
                return [f"{name}[{i}] is not {{text, fields}}"]
            statements.append((f"{name}[{i}]", it["text"], it["fields"]))
    if not out["criteria_met"]:
        problems.append("criteria_met is empty")

    allowed: set[float] = set()
    _numbers({k: payload[k] for k in ("cluster", "criteria", "grouping", "members")}, allowed)
    vocab = _vocabulary(payload)
    durations = {float(payload["cluster"]["time_span_hours"])} | {
        float(v["threshold"]) for k, v in payload["criteria"].items() if k == "time_window_hours"}
    names = [*destination_labels, *outcome_names, *(n.replace("_", " ") for n in outcome_names)]
    named = re.compile("|".join(re.escape(n) for n in sorted(names, key=len, reverse=True)) or r"(?!x)x",
                       re.IGNORECASE)

    for where, text, fields in statements:
        if not fields:
            problems.append(f"{where} cites no cluster field")
        unknown = [f for f in fields if f not in FIELDS]
        if unknown:
            problems.append(f"{where} cites fields that do not exist: {unknown}")
        own = vocab.sub(" ", text.replace("-", " "))
        if _DECISION.search(own) or named.search(own) or _ROUTING.search(own):
            problems.append(f"{where} contains routing, decision or recommendation language")
        if _SUSPICION.search(own):
            problems.append(f"{where} implies suspicion or wrongdoing")
        if _JUDGEMENT.search(own):
            problems.append(f"{where} contains evaluative judgement: {_JUDGEMENT.search(own).group()!r}")
        if _SCREENING_MATCH.search(own):
            problems.append(f"{where} states a screening match")
        if _PROCEDURE.search(own):
            problems.append(f"{where} is an instruction, not an explanation")
        for m in _NUMBER.finditer(text):
            if _as_number(m.group()) not in allowed:
                problems.append(f"{where} states a number not in the cluster data: {m.group()}")
        for m in _DURATION.finditer(text):
            if not (m.group(2).lower().startswith("h") and float(m.group(1)) in durations):
                problems.append(f"{where} states a duration the cluster data does not give: {m.group()!r}")
    return problems


def _empty(status: str, model: str | None, reason: str) -> dict:
    return {"status": status, "generated_by": None, "model": model, "summary": None,
            "summary_fields": [], "criteria_met": [], "characteristics": [], "reason": reason}


def summarize_cluster(analyst: AIAnalyst, payload: dict, destination_labels: list[str]) -> dict:
    """Never raises. Same states as the alert summary: generated, unavailable, error."""
    if not analyst.available:
        return _empty(STATUS_UNAVAILABLE, analyst.model, f"AI summary unavailable: {analyst.unavailable_reason}")
    try:
        resp = analyst.client.responses.create(
            model=analyst.model,
            instructions=CLUSTER_INSTRUCTIONS,
            input=json.dumps(payload, ensure_ascii=False),
            text={"format": {"type": "json_schema", "name": "cluster_summary",
                             "schema": cluster_output_schema(), "strict": True}},
        )
    except Exception as exc:  # any SDK, network or timeout failure
        log.warning("AI cluster summary request failed: %s", type(exc).__name__)
        return _empty(STATUS_ERROR, analyst.model, f"AI summary request failed ({type(exc).__name__})")
    status = getattr(resp, "status", "completed")
    if status != "completed":
        return _empty(STATUS_ERROR, analyst.model, f"AI summary response was not completed (status: {status})")
    try:
        out = json.loads(getattr(resp, "output_text", "") or "")
    except (TypeError, ValueError):
        return _empty(STATUS_ERROR, analyst.model, "AI summary response was not valid JSON")
    problems = check_cluster_output(out, payload, destination_labels, analyst.outcome_names)
    if problems:
        log.warning("AI cluster summary rejected: %s", problems)
        return _empty(STATUS_ERROR, analyst.model, "AI summary rejected by checks: " + "; ".join(problems[:5]))
    return {"status": STATUS_GENERATED, "generated_by": "llm", "model": analyst.model,
            **{k: out[k] for k in _OUT}, "reason": None}
