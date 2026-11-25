"""
AI analyst summary.

The only place in this project that calls a language model.

Position in the flow:

    alert -> InvestigationEngine.investigate() -> EvidencePack
          -> build_summary_input()  (a subset of build_llm_input())
          -> model                  (OpenAI, model name from LLM_MODEL, else OPENAI_MODEL)
          -> check_output()         (deterministic grounding checks)
          -> AI analyst summary     -> human analyst

The model explains evidence that the deterministic investigation has already
collected and classified as FOUND or MISSING. It does not select tools, fetch
data, decide what evidence is required, judge risk, recommend an outcome or make
any decision. Its output is accepted only if every statement cites evidence keys
that exist in the pack and passes the checks in check_output(); otherwise the
caller gets status "error" and no summary.

The summary is a short narrative written as a list of sentences, each citing the
evidence it states, so every sentence is checked on its own. The API joins them
into one `summary` string; the sentences about MISSING items are also returned
as `missing_information`.

Nothing in this module names an alert, rule, category, playbook or evidence key.
All of those come from the pack at request time, so the KB stays the single
place procedure is defined.

Failure never propagates: a missing key, an SDK error, a timeout or a rejected
output all become a summary dict with status "unavailable" or "error".
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

from backend.investigation import EvidencePack, InvestigationEngine
from backend.tools.datastore import MISSING

log = logging.getLogger("uc3.ai")

API_KEY_ENV = "OPENAI_API_KEY"
MODEL_ENV = "LLM_MODEL"
LEGACY_MODEL_ENV = "OPENAI_MODEL"  # still read when LLM_MODEL is not set
REQUEST_TIMEOUT_SECONDS = 60.0

STATUS_GENERATED = "generated"
STATUS_UNAVAILABLE = "unavailable"
STATUS_ERROR = "error"

INSTRUCTIONS = """\
You write a short, AI-generated narrative for a human compliance analyst. It explains, in
plain professional sentences, the evidence that a deterministic investigation of one alert
has already collected.

The user message is that completed investigation. It is your only source of facts. You do
not investigate, decide or advise. The analyst makes every decision.

OUTPUT
1. summary_sentences: 3 to 6 complete sentences, in this order. Leave out a part when there
   is no evidence for it; never pad.
   a. The alert type, from playbook.category with underscores as spaces and acronyms in
      capitals, joining two adjacent acronyms or an acronym list with a slash: "An alert
      for <category words> has been raised." This sentence states nothing else and cites
      no evidence.
   b. The main observed values, in natural language, e.g. "The available data shows
      <readable name> of <value>, with customer and programme baselines of <n> and <m>
      respectively." Combine related values in one sentence. Write a count of countable
      things the way an analyst would say it ("25 failed transactions", "8 top-ups"),
      and an amount or activity measure with "of" ("transaction activity of 153"); never
      put a number straight before a name that is not a countable noun. Spell topup as
      "top-up" everywhere, including the alert type.
   c. Verification and enrichment, e.g. "The KYB status is recorded as <value>, the
      beneficial owner status is <value>, and the source of funds is <value>."
   d. Records and history, e.g. "<n> transaction records were retrieved, and no prior
      alerts are recorded."
   e. Missing evidence, in its own sentence that mentions only missing items, with the verb
      agreeing: one item "<Readable name> is not available in the collected evidence.";
      two "<Name A> and <name B> are not available in the collected evidence."; more
      "<A>, <B> and <C> are not available in the collected evidence." Give no reason.
   Each sentence cites every evidence key whose value or name it states. State each value
   once. Write prose: never write "<Name>: <value>" in a sentence and never use a colon.
   Start every sentence and item with a capital letter.
   Group related facts in one sentence, but keep each sentence to about four facts;
   split a longer list into two sentences. Aim for 4 to 6 sentences in total.

WRITING
   - Make every noun and verb agree with its number: "1 prior alert is recorded", "2 prior
     alerts are recorded", "1 transaction record was retrieved", "11 transaction records
     were retrieved", "1 previous screening review", "3 previous screening reviews".
   - Name a count by the things it counts, keeping every other word of the name: a
     "failed transaction count" of 25 is "25 failed transactions", a "prior alert count"
     of 2 is "2 prior alerts", a "previous screening review count" of 1 is "1 previous
     screening review". Never rename an item to a different concept, and keep "count"
     only where dropping it would change the meaning.
   - When an item's own name contains a word such as share, rate, ratio or spread, always
     use its full name ("top example share of 0.5"), never the bare word ("a share of
     0.5"): on its own that word relates numbers, which is not allowed.
   - Never repeat a name inside its own value: "Review required is recorded as true",
     not "Review required: review required is recorded as true"; "The screening status
     is recorded as review required".
   - Use no article before a name that reads as a flag ("Review required is recorded as
     true", not "The review required is recorded as true"), and write it in lower case
     in the middle of a sentence ("…, and review required is recorded as true").
   - This is wording only. Never add interpretation: not "unusually high", not "appears
     to have unresolved issues", not "may represent a risk".
2. key_findings: 2 to 4 items for a quick scan, each about one evidence item, in the form
   "<Readable name>: <value>", the value alone after the colon ("Review required: true",
   not "Review required: is recorded as true"; "Failed transactions: 25", not "Failed
   transactions: 25 failed transactions"; a list as readable values separated by commas,
   "AE, CY"). A missing item may be one, as "<Readable name>: not available".
   Choose the facts most central to the alert type; do not repeat the whole evidence
   list, and do not use a list of records as a finding.
3. analyst_attention: 0 to 2 items, each of exactly one of these kinds. Choose the kind
   first, then write the text in its form. If none exists, return an empty list: an empty
   list is better than a weak observation.
   - missing_evidence: a MISSING item. Form: "<Readable name>: not available in the
     collected evidence."
   - same_value_in_items: the same text value (a country, category, counterparty or other
     identifier, never a number) appears in two or more FOUND items. The values must be
     exactly identical: a value that only contains another (example and example_local)
     is not the same value. Form: "<value> appears
     in both <Readable name A> and <Readable name B>." (Capitalise the first word.)
   - observed_vs_baseline: an observed count and an item whose name says it is a baseline
     for that count. Form: "<Readable name>: <n>; <Readable baseline name>: <m>."
   Never write an instruction: no opening verb such as investigate, check, review, consider,
   verify or request, and never should, must or need to.
4. Every item cites the evidence keys it relies on, using only keys present in `evidence`.

FACTS AND VALUES
5. Use only values present in `evidence`. No general knowledge, assumptions, behaviour,
   intent, risk judgements or conclusions.
6. Copy each value with underscores replaced by spaces and keep all of its words: a value
   example_value_declared is written "example value declared", never "example value". Never
   reword a value ("complete", not "completed"; "review required", not "match").
7. A true/false value is written "<readable field name> is recorded as true" (or false),
   naming it after the field with all of its words (field corporate_example_available is
   "corporate example available is recorded as false", never "corporate example is
   false"). Never restate it in other words.
8. Write numbers as digits (868, not eight hundred and sixty-eight). Use only numbers in
   evidence values, or the number of items in a list value. A count of 0 may be written as
   "no" ("no prior alerts are recorded").
9. Copy dates and identifiers exactly. Give a timestamp as its date part only, in natural
   wording ("last screened on 2026-01-31").
10. `args` are how evidence was retrieved, not evidence: never quote them. No value declares
   the period it covers, so never state or imply a time period, and never use the words
   window, lookback or period.

NUMBERS ARE NEVER RELATED TO EACH OTHER
11. You may state several values in one sentence, and a baseline beside the value it is
   named for. Never relate two numbers: no "out of", "of which", "representing",
   "accounting for", "share of", percentages, ratios, rates, "times", "twice",
   differences, "above", "below", "higher", "lower", "more than", "less than", "exceeds",
   "compared with", increase or rise. Do not compute anything.
12. A baseline is a baseline. There is no alert threshold in the evidence: never mention a
   threshold or limit, and never say why the alert was raised or what triggered it.

MISSING EVIDENCE
13. status MISSING means the information was not available. Name every MISSING item in the
   missing-evidence sentence (1e). Never infer or imply its value, and never describe it as
   false, failed, negative, no, none, zero, rejected or suspicious.

SCREENING
14. Report screening evidence exactly as supplied: the status, review flag and confidence
   band as values. Never call a result a "match", "PEP match", "sanctions match" or
   "confirmed" unless a supplied value says exactly that.

NO JUDGEMENT, NO DECISIONS
15. No evaluative words: not notably, significantly, substantially, unusually, abnormally,
   concerning, worrying, alarming, risky, inconsistent, consistent, discrepancy, anomaly.
   No causes or inferences: not because, due to, therefore, indicates, suggests, likely,
   appears to. The words of playbook.category are allowed in the alert-type sentence.
16. No decision, outcome, escalation, clearing or confirming, confidence level, risk score or
   routing. Do not use the words recommend, escalate, suspicious, fraud, or confidence
   (except as part of a supplied field name such as a match confidence band). Obey
   playbook.ai_boundaries and global_boundaries.

READABLE NAMES
17. Refer to evidence by readable names, never raw keys: replace underscores with spaces,
   keep acronyms such as KYB, KYC and PEP in capitals, in names and in values alike ("KYB
   status", "beneficial owner status", "source of funds", "PEP domestic"). If the field name is clearer than the key, use it ("prior
   alert count").

EXAMPLE OF THE STYLE (invented values; never reuse them)
summary_sentences:
  "An alert for example activity pattern has been raised."
  "The available data shows event activity of 40, with customer and programme baselines of
   12 and 15 respectively."
  "The KYB status is recorded as complete, and the source of funds is example income
   declared."
  "6 event records were retrieved, and no prior alerts are recorded."
  "Beneficial owner status is not available in the collected evidence."
key_findings: "Event activity: 40", "Customer event baseline: 12", "Beneficial owner status:
  not available"
"""

# --------------------------------------------------------------------------
# Input
# --------------------------------------------------------------------------

# Keys of the KB's global_ai_boundaries passed to the model. model_may and
# outcome_contract are deliberately excluded: both permit recommending an
# outcome, which this feature does not do.
_GLOBAL_BOUNDARY_KEYS = ("model_must_not", "screening_specific")


def build_summary_input(engine: InvestigationEngine, pack: EvidencePack) -> dict:
    """
    The exact message sent to the model, projected from build_llm_input().

    Excluded on purpose: permitted_outcomes, escalation_guidance (decision
    material), checklist (procedure), and the alert's created_at (it would
    invite the model to anchor a time period the data does not declare).
    """
    full = engine.build_llm_input(pack)
    alert = {k: v for k, v in full["alert"].items() if k != "category"}
    return {
        "alert": alert,
        "playbook": {
            "playbook_id": full["playbook_id"],
            "category": full["alert"]["category"],
            "investigation_focus": full["investigation_focus"],
            "ai_boundaries": full["boundaries"],
        },
        "global_boundaries": {
            k: full["global_boundaries"][k]
            for k in _GLOBAL_BOUNDARY_KEYS if k in full["global_boundaries"]
        },
        "evidence": full["evidence"],
        "missing_evidence": full["missing_evidence"],
    }


# --------------------------------------------------------------------------
# Output contract
# --------------------------------------------------------------------------

# What the model returns. The narrative is a list of sentences so each one is
# checked against the evidence it cites; the API joins them into `summary`.
_ITEM_LISTS = ("summary_sentences", "key_findings", "analyst_attention")
# The only observations analyst_attention may make. Each is verified against
# the cited evidence in _attention_kinds_met(), so the kind is never trusted.
ATTENTION_KINDS = ("missing_evidence", "same_value_in_items", "observed_vs_baseline")
_OUTPUT_FIELDS = _ITEM_LISTS
# Shape limits. The prompt asks for 3-6 sentences and 2-4 findings; the checks
# allow a little either side so a concise answer is not rejected for length.
SUMMARY_SENTENCES = (2, 8)
KEY_FINDINGS = (2, 4)
MAX_ATTENTION = 2


def output_schema(evidence_keys: list[str]) -> dict:
    """
    Strict JSON schema for the model's answer. The enum of citable keys is the
    pack's own key list, so the model cannot cite a key that was not collected.
    """
    key_list = {"type": "array", "items": {"type": "string", "enum": list(evidence_keys)}}

    def item() -> dict:
        return {
            "type": "object",
            "additionalProperties": False,
            "required": ["text", "evidence_keys"],
            "properties": {"text": {"type": "string"}, "evidence_keys": key_list},
        }

    # Analyst attention items declare their kind before their text, so the
    # model commits to one of the allowed observation types first.
    attention_item = {
        "type": "object",
        "additionalProperties": False,
        "required": ["kind", "text", "evidence_keys"],
        "properties": {
            "kind": {"type": "string", "enum": list(ATTENTION_KINDS)},
            "text": {"type": "string"},
            "evidence_keys": key_list,
        },
    }

    return {
        "type": "object",
        "additionalProperties": False,
        "required": list(_OUTPUT_FIELDS),
        "properties": {
            "summary_sentences": {"type": "array", "items": item()},
            "key_findings": {"type": "array", "items": item()},
            "analyst_attention": {"type": "array", "items": attention_item},
        },
    }


# --------------------------------------------------------------------------
# Grounding checks
#
# These are guards on the model's wording, not business logic: none of them
# names a category, rule or evidence key. The schema enum already prevents an
# unknown key; check_output() repeats that and adds what a schema cannot express.
# --------------------------------------------------------------------------

# Words that would turn "not available" into a conclusion.
_NEGATIVE = re.compile(
    r"\b(false|fail\w*|negative|no|none|zero|nil|rejected|denied|suspicious|clean|cleared)\b",
    re.IGNORECASE)
# A statement about a MISSING item must say it was not available.
_AVAILABILITY = re.compile(
    r"\b(not available|unavailable|missing|not provided|not returned|not supplied)\b",
    re.IGNORECASE)
# Decision, recommendation and scoring language, anywhere in the output.
_DECISION = re.compile(
    r"\b(recommend\w*|escalat\w*|outcome\w*|confidence|risk score|risk rating|suspicious|fraud\w*|"
    r"cleared|false positive|true positive|no further action|close the alert)\b",
    re.IGNORECASE)
# Procedural wording in analyst_attention: an imperative opening or a modal.
_PROCEDURE = re.compile(
    r"(^\s*(investigate|check|review|consider|verify|confirm|request|obtain|contact|"
    r"assess|determine|ensure|escalate|look)\b)|\b(should|must|needs? to|ought to)\b",
    re.IGNORECASE)
# Time periods the data does not declare.
_PERIOD = re.compile(
    r"\b\d+\s*-?\s*(hours?|days?|weeks?|months?|years?)\b|"
    r"\b(last|past|previous|recent)\s+(\d+\s+)?(hours?|days?|weeks?|months?|years?)\b|"
    r"\b(daily|weekly|monthly|per (hour|day|week|month)|window|lookback)\b",
    re.IGNORECASE)
# Evaluative wording and unsupported comparisons: the model states values, the
# analyst judges them.
_JUDGEMENT = re.compile(
    r"\b(notabl[ey]|significant(ly)?|substantial(ly)?|great(ly)?|sharp(ly)?|marked(ly)?|"
    r"dramatic(ally)?|unusual(ly)?|abnormal(ly)?|concerning|worrying|alarming|"
    r"(in)?consisten(t|cy|cies)|discrepanc(y|ies)|anomal(y|ies|ous)|mismatch\w*|"
    r"outnumber\w*|(more|less|higher|lower|fewer) than expected)\b",
    re.IGNORECASE)
# Screening overstated as an established match.
_OVERSTATED_MATCH = re.compile(
    r"\b(confirmed|positive|true|definite|actual)\s+(\w+\s+)?match(es)?\b|"
    r"\b(returned|produced|found|identified|is|was)\s+an?\s+(\w+\s+)?match\b|"
    r"\b(pep|sanctions?)\s+match(es|ed)?\b|\bmatched\b",
    re.IGNORECASE)
# A standalone number; IDs such as TXN-0514-000 or P002 are not numbers.
_NUMBER = re.compile(r"(?<![A-Za-z0-9_\-.])\d+(?:[.,]\d+)*")
# A relationship between numbers that the evidence does not supply: a part of a
# whole, a proportion, a multiple, a difference or a direction of change. Values
# may be stated side by side; they may not be related.
_RELATION = re.compile(
    r"%|\b(out of|of which|of whom|representing|represents?|accounting for|accounts? for|"
    r"share of|proportion\w*|percent\w*|per cent|ratios?|rates?|times|twice|doubled?|tripled?|"
    r"half|exceed\w*|above|below|higher|lower|greater|fewer|more than|less than|"
    r"compared|comparison|relative to|versus|vs|difference|differs?|increase[ds]?|decrease[ds]?|"
    r"ris(e|es|en|ing)|rose|drop(s|ped)?|spike[ds]?|jump(s|ed)?|surge[ds]?)\b",
    re.IGNORECASE)
# Causes, inferences and thresholds the evidence does not contain.
_INFERENCE = re.compile(
    r"\b(because|due to|therefore|thus|hence|as a result|indicat\w*|suggest\w*|impl(y|ies|ied)|"
    r"(un)?likely|probabl[ey]|appears? to|seems?|threshold\w*|limits?|trigger\w*|breach\w*|"
    r"risky|riskier|intent\w*|behaviou?r\w*|unexplained|explain\w*)\b",
    re.IGNORECASE)
# Numbers must be written as digits so they can be checked against the evidence.
_NUMBER_WORD = re.compile(
    r"\b(one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|"
    r"fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|"
    r"eighty|ninety|hundred|thousand|million|dozen)\b",
    re.IGNORECASE)
# A "Name: value" pair. The narrative is prose; a colon between digits (a time
# inside a copied timestamp) is not a pair.
_FIELD_VALUE = re.compile(r"(?<!\d):|:(?!\d)")


def _numbers_in_evidence(evidence: dict) -> set[float]:
    found: set[float] = set()

    def walk(v: Any) -> None:
        if isinstance(v, bool):
            return
        if isinstance(v, (int, float)):
            found.add(float(v))
        elif isinstance(v, str):
            for m in _NUMBER.finditer(v):
                found.add(_as_number(m.group()))
        elif isinstance(v, list):
            found.add(float(len(v)))
            for x in v:
                walk(x)
        elif isinstance(v, dict):
            for x in v.values():
                walk(x)

    for item in evidence.values():
        walk(item["value"])
    return found


def _as_number(token: str) -> float:
    if re.fullmatch(r"\d{1,3}(,\d{3})+(\.\d+)?", token):
        token = token.replace(",", "")
    try:
        return float(token)
    except ValueError:
        return float(token.replace(",", "."))


def _evidence_vocabulary(evidence: dict) -> list[str]:
    """
    Wording that comes from the evidence itself: keys, field names and string
    values, plus every run of two or more words inside a key or field name.
    Removed (whole words only) from a statement before the decision-language
    check, so reporting a fact - a status value that happens to equal an outcome
    name, or a field whose name contains "confidence" - is not mistaken for a
    decision. Longest first, so the widest phrase is removed.
    """
    terms: set[str] = set()

    def strings(v: Any) -> None:
        if isinstance(v, str):
            terms.update({v, v.replace("_", " ")})
        elif isinstance(v, list):
            for x in v:
                strings(x)
        elif isinstance(v, dict):
            for x in v.values():
                strings(x)

    for key, item in evidence.items():
        for name in (key, item["field"]):
            words = name.split("_")
            terms.add(name)
            terms.update(" ".join(words[i:j]) for i in range(len(words))
                         for j in range(i + 2, len(words) + 1))
        strings(item["value"])
    return sorted((t for t in terms if t.strip()), key=len, reverse=True)


def _shape_problems(out: Any) -> list[str]:
    if not isinstance(out, dict):
        return ["output is not a JSON object"]
    problems = []
    if set(out) != set(_OUTPUT_FIELDS):
        problems.append(f"output fields {sorted(out)} do not match {sorted(_OUTPUT_FIELDS)}")
        return problems
    for name in _ITEM_LISTS:
        items = out[name]
        if not isinstance(items, list):
            problems.append(f"{name} is not a list")
            continue
        allowed = ({frozenset({"text", "evidence_keys"}), frozenset({"kind", "text", "evidence_keys"})}
                   if name == "analyst_attention" else {frozenset({"text", "evidence_keys"})})
        for i, it in enumerate(items):
            if not (isinstance(it, dict) and frozenset(it) in allowed
                    and isinstance(it["text"], str) and it["text"].strip()
                    and isinstance(it["evidence_keys"], list)
                    and all(isinstance(k, str) for k in it["evidence_keys"])
                    and it.get("kind", ATTENTION_KINDS[0]) in ATTENTION_KINDS):
                problems.append(f"{name}[{i}] is not {{text, evidence_keys}}")
    if problems:
        return problems
    if not out["summary_sentences"]:
        problems.append("summary is empty")
    return problems


def _readable(s: str) -> str:
    """Lower case, with underscores, hyphens and slashes as single spaces."""
    return re.sub(r"[\s_\-/]+", " ", s.lower()).strip()


def _value_stated(text: str, value: Any) -> bool:
    """
    Whether a FOUND value is actually stated in the text, in any natural
    wording around it. Strings must appear with every word (underscores as
    spaces); numbers as an equal number, or "no"/"zero" for 0; true/false as
    the word. A timestamp may appear as its date. Lists and records are checked
    by the number rules instead (a count, or nothing).
    """
    if isinstance(value, bool):
        return re.search(rf"\b{str(value).lower()}\b", text.lower()) is not None
    if isinstance(value, (int, float)):
        if float(value) in {_as_number(m.group()) for m in _NUMBER.finditer(text)}:
            return True
        return value == 0 and re.search(r"\b(no|zero)\b", text, re.IGNORECASE) is not None
    if isinstance(value, str):
        # "top-up" in the text is the same word as "topup" in a value.
        if any(re.search(rf"(?<!\w){re.escape(_readable(value))}(?!\w)", _readable(t))
               for t in (text, text.replace("-", ""))):
            return True
        date = re.match(r"(\d{4}-\d{2}-\d{2})T", value)
        return bool(date and date.group(1) in text)
    return True


def _names_item(text: str, key: str, field: str) -> bool:
    """
    Whether the text names an evidence item: every word of its key or of its
    field name appears (singular or plural), so a readable name built from the
    key's words counts but "some information" names nothing.
    """
    # "top-up" also counts as "topup", the spelling evidence names use.
    words = re.findall(r"[a-z0-9]+", text.lower()) + re.findall(r"[a-z0-9]+", text.lower().replace("-", ""))
    return any(all(any(w.startswith(part.rstrip("s") or part) for w in words)
                   for part in name.lower().split("_"))
               for name in (key, field))


def _mentioned_items(text: str, evidence: dict, cited: list[str] = ()) -> set[str]:
    """
    Evidence items whose multi-word readable name (from the key or the field)
    appears in the text as a phrase. A statement that names an item must cite
    it, so a fact cannot be slipped in under another item's citation. A name
    found only inside a longer item name ("transaction count" inside "failed
    transaction count") belongs to the longer one. The values of the cited
    items are not names: a screening status of "review required" does not
    mention the item named review required. "top-up" is read as "topup" too,
    so the hyphenated spelling cannot hide a name.
    """
    hits: set[str] = set()
    for variant in (text, text.replace("-", "")):
        t = _readable(variant)
        for k in cited:
            v = evidence.get(k, {}).get("value")
            if isinstance(v, str) and v.strip():
                t = re.sub(rf"(?<!\w){re.escape(_readable(v))}(?!\w)", " ", t)
        spans: list[tuple[int, int, str]] = []
        for k, e in evidence.items():
            for name in (k, e["field"]):
                words = name.lower().split("_")
                if len(words) >= 2:
                    pat = r"\b" + r"\s+".join(re.escape(w) for w in words) + r"s?\b"
                    spans += [(m.start(), m.end(), k) for m in re.finditer(pat, t)]
        hits |= {k for s, e, k in spans
                 if not any(s2 <= s and e <= e2 and (e2 - s2) > (e - s) for s2, e2, _ in spans)}
    return hits


def _category_word(w: str) -> str:
    """A category word as a pattern that also accepts it hyphenated ("top-up" for topup)."""
    return r"\b" + r"-?".join(re.escape(c) for c in w) + r"\w*"


def _names_category(text: str, category: str) -> bool:
    """The alert-type sentence names every word of the playbook category."""
    t = text.lower().replace("/", " ")
    return all(re.search(_category_word(w), t) for w in category.lower().split("_") if w)


def _atoms(v: Any) -> set[str]:
    """Non-numeric text values inside an evidence value, normalised for comparison."""
    if isinstance(v, str):
        return {v.strip().lower()} if v.strip() and not _NUMBER.fullmatch(v.strip()) else set()
    if isinstance(v, list):
        return set().union(*(_atoms(x) for x in v)) if v else set()
    return set()


def _attention_kinds_met(keys: list[str], evidence: dict) -> set[str]:
    """
    Which allowed observation kinds the cited evidence actually supports.
    Deterministic, from the evidence values and names only.
    """
    cited = [evidence[k] for k in keys if k in evidence]
    if not cited or len(cited) != len(keys):
        return set()
    met = set()
    if all(e["status"] == MISSING for e in cited):
        met.add("missing_evidence")
    found = [(k, evidence[k]) for k in keys if evidence[k]["status"] != MISSING]
    if len(found) == len(cited) and len(found) >= 2:
        counts: dict[str, int] = {}
        for _, e in found:
            for a in _atoms(e["value"]):
                counts[a] = counts.get(a, 0) + 1
        if any(n >= 2 for n in counts.values()):
            met.add("same_value_in_items")
        numeric = all(isinstance(e["value"], (int, float)) and not isinstance(e["value"], bool)
                      for _, e in found)
        is_base = ["baseline" in f"{k} {e['field']}".lower() for k, e in found]
        if numeric and any(is_base) and not all(is_base):
            met.add("observed_vs_baseline")
    return met


def check_output(out: Any, payload: dict, outcome_names: list[str]) -> list[str]:
    """
    Return every reason the model's answer must be rejected. Empty means it is
    grounded in the payload and contains no decision language.

    Natural sentences are accepted when their claims are grounded: the checks
    look at what a sentence states (its values, numbers, names and wording),
    not at a fixed "Name: value" format.
    """
    problems = _shape_problems(out)
    if problems:
        return problems

    evidence = payload["evidence"]
    category = payload["playbook"]["category"]
    missing = {k for k, v in evidence.items() if v["status"] == MISSING}
    allowed_numbers = _numbers_in_evidence(evidence)

    def phrase_pattern(terms: list[str]) -> re.Pattern:
        return re.compile(r"(?<!\w)(" + ("|".join(re.escape(t) for t in terms) or r"(?!x)x")
                          + r")(?!\w)", re.IGNORECASE)

    # The evidence's own wording, and the alert's category (named in the first
    # sentence), are removed before the wording checks, so reporting a fact is
    # never mistaken for a judgement or a decision.
    vocab_terms = _evidence_vocabulary(evidence)
    vocab_terms += [category, category.replace("_", " ")]
    vocabulary = phrase_pattern(sorted(set(vocab_terms), key=len, reverse=True))
    # Only the names of items (never their values) are removed before the
    # negative-wording check on MISSING statements, so a missing item named
    # e.g. "failed check count" can be reported without "failed" counting.
    names_only = phrase_pattern(sorted(
        {t for k, e in evidence.items() for n in (k, e["field"]) for t in (n, n.replace("_", " "))},
        key=len, reverse=True))
    outcome_text = re.compile(
        "|".join(re.escape(n) for n in sorted(
            {*outcome_names, *(n.replace("_", " ") for n in outcome_names)}, key=len,
            reverse=True)) or r"(?!x)x",
        re.IGNORECASE)

    n_sent = len(out["summary_sentences"])
    if not SUMMARY_SENTENCES[0] <= n_sent <= SUMMARY_SENTENCES[1]:
        problems.append(f"summary has {n_sent} sentences; expected "
                        f"{SUMMARY_SENTENCES[0]}-{SUMMARY_SENTENCES[1]}")
    lo, hi = min(KEY_FINDINGS[0], len(evidence)), KEY_FINDINGS[1]
    if not lo <= len(out["key_findings"]) <= hi:
        problems.append(f"key_findings has {len(out['key_findings'])} items; expected {lo}-{hi}")
    if len(out["analyst_attention"]) > MAX_ATTENTION:
        problems.append(f"analyst_attention has more than {MAX_ATTENTION} items")

    first = out["summary_sentences"][0]["text"] if out["summary_sentences"] else ""
    if first and not _names_category(first, category):
        problems.append("summary does not begin by naming the alert type")

    statements: list[tuple[str, str, list[str]]] = []
    for name in _ITEM_LISTS:
        statements += [(f"{name}[{i}]", it["text"], it["evidence_keys"])
                       for i, it in enumerate(out[name])]

    covered_missing: set[str] = set()
    for where, text, keys in statements:
        is_sentence = where.startswith("summary_sentences")
        is_alert_type = where == "summary_sentences[0]"
        unknown = [k for k in keys if k not in evidence]
        if unknown:
            problems.append(f"{where} cites evidence that does not exist: {unknown}")
            continue
        if not keys and not is_alert_type:
            problems.append(f"{where} cites no evidence")
        if not keys and _NUMBER.search(text):
            problems.append(f"{where} states a number without citing evidence")

        # The alert-type sentence is read without the category's own words
        # ("unusual" in "unusual top-up pattern" is the category, not a judgement).
        text_wo = text
        if is_alert_type:
            for w in category.lower().split("_"):
                if w:
                    text_wo = re.sub(_category_word(w), " ", text_wo, flags=re.IGNORECASE)
        # Hyphens become spaces first, so "match-confidence band" is recognised as
        # the evidence's own field name.
        own_words_removed = vocabulary.sub(" ", text_wo.replace("-", " "))
        if _DECISION.search(own_words_removed) or outcome_text.search(own_words_removed):
            problems.append(f"{where} contains decision or recommendation language")
        if _JUDGEMENT.search(own_words_removed):
            problems.append(f"{where} contains evaluative or comparative judgement: "
                            f"{_JUDGEMENT.search(own_words_removed).group()!r}")
        if _RELATION.search(own_words_removed):
            problems.append(f"{where} relates numbers in a way the evidence does not supply: "
                            f"{_RELATION.search(own_words_removed).group()!r}")
        if _INFERENCE.search(own_words_removed):
            problems.append(f"{where} states a cause, inference or threshold: "
                            f"{_INFERENCE.search(own_words_removed).group()!r}")
        if _NUMBER_WORD.search(own_words_removed):
            problems.append(f"{where} writes a number in words: "
                            f"{_NUMBER_WORD.search(own_words_removed).group()!r}")
        if _OVERSTATED_MATCH.search(own_words_removed):
            problems.append(f"{where} states a match the evidence does not establish")
        if _PERIOD.search(text):
            problems.append(f"{where} states a time period the evidence does not declare")
        if is_sentence and _FIELD_VALUE.search(text):
            problems.append(f"{where} is written as 'Name: value', not as a sentence")
        for m in _NUMBER.finditer(text):
            if _as_number(m.group()) not in allowed_numbers:
                problems.append(f"{where} states a number not present in the evidence: "
                                f"{m.group()}")

        # A named item must be cited, so a fact cannot ride on another citation.
        # The alert-type sentence is read without the category's own words.
        uncited = sorted(_mentioned_items(text_wo, evidence, keys) - set(keys))
        if uncited:
            problems.append(f"{where} names evidence it does not cite: {uncited}")

        cites_missing = [k for k in keys if k in missing]
        cites_found = [k for k in keys if k not in missing]
        # Every FOUND value cited must actually be stated, in whatever wording.
        if where.startswith(("summary_sentences", "key_findings")):
            for k in cites_found:
                if not _value_stated(text, evidence[k]["value"]):
                    problems.append(f"{where} cites {k!r} but does not state its value "
                                    f"{evidence[k]['value']!r}")
                # A true/false value means nothing without the full name it answers:
                # "corporate structure is false" drops the "available" it is about.
                elif (isinstance(evidence[k]["value"], bool)
                      and not _names_item(text, evidence[k]["field"], evidence[k]["field"])):
                    problems.append(f"{where} states {k!r} as true/false without its full "
                                    f"name {evidence[k]['field']!r}")
        if cites_missing:
            if not _AVAILABILITY.search(text):
                problems.append(f"{where} cites MISSING {cites_missing} without saying "
                                f"it was not available")
            if _NEGATIVE.search(names_only.sub(" ", text.replace("-", " "))):
                problems.append(f"{where} turns MISSING {cites_missing} into a "
                                f"negative conclusion")
            if where.startswith(("summary_sentences", "key_findings")):
                if cites_found:
                    problems.append(f"{where} mixes MISSING {cites_missing} with FOUND "
                                    f"evidence; missing items get their own sentence")
                for k in cites_missing:
                    if not _names_item(text, k, evidence[k]["field"]):
                        problems.append(f"{where} does not name the missing item {k!r}")
                if is_sentence:
                    covered_missing |= set(cites_missing)
        if where.startswith("key_findings") and len(keys) != 1:
            problems.append(f"{where} must state exactly one evidence item")

    if missing - covered_missing:
        problems.append(f"summary omits MISSING evidence: {sorted(missing - covered_missing)}")

    for i, it in enumerate(out["analyst_attention"]):
        # The evidence's own words are removed first, as for decision language,
        # so a field named e.g. "review required" is not read as an instruction.
        if _PROCEDURE.search(vocabulary.sub(" ", it["text"].replace("-", " "))):
            problems.append(f"analyst_attention[{i}] is an instruction, not an observation")
        # Every attention item must be one of the allowed observations, verified
        # against the evidence it cites: a MISSING item, the same text value in
        # two items, or a count beside its named baseline. A declared kind must
        # be one the evidence supports.
        met = _attention_kinds_met(it["evidence_keys"], evidence)
        if not met:
            problems.append(f"analyst_attention[{i}] is not a supported observation "
                            f"(missing item, shared value, or count beside its baseline)")
        elif "kind" in it and it["kind"] not in met:
            problems.append(f"analyst_attention[{i}] declares kind {it['kind']!r} but the "
                            f"cited evidence supports {sorted(met)}")
    return problems


# --------------------------------------------------------------------------
# The analyst
# --------------------------------------------------------------------------

# --------------------------------------------------------------------------
# Wording polish
#
# Spelling, casing and agreement only, applied to the model's answer BEFORE the
# checks, so what is checked is exactly what the analyst reads. Nothing here
# adds, removes or changes a value, a number or a citation: a polished answer
# that states something unsupported is still rejected.
# --------------------------------------------------------------------------

_ACRONYM = re.compile(r"\b(pep|kyb|kyc|mlro|mcc|ubo)(s?)\b", re.IGNORECASE)
_TOPUP = re.compile(r"\b([Tt])op ?ups?\b|\b([Tt])opup(s?)\b")
# "1 prior alerts are recorded" -> "1 prior alert is recorded". Only a count of
# exactly 1, only a plain plural ending in s (not -ss or -ies), only before
# are/were, so nothing else in the sentence can change.
_ONE_PLURAL = re.compile(r"\b1 ((?:[A-Za-z-]+ ){0,3})([A-Za-z-]*[^\Ws_])(?<!ie)s (are|were)\b")
_COPULA = re.compile(r"^(?:(?:is|are|was|were)\s+)?(?:recorded\s+as\s+|shown\s+as\s+)?", re.IGNORECASE)


def _polish_text(text: str) -> str:
    t = text.strip()
    t = _TOPUP.sub(lambda m: (m.group(1) or m.group(2)) + "op-up"
                   + ("s" if m.group(0).lower().endswith("s") else ""), t)
    t = _ACRONYM.sub(lambda m: m.group(1).upper() + m.group(2).lower(), t)
    t = _ONE_PLURAL.sub(lambda m: m.group(0) if re.search(r"(ss|x|z|ch|sh)e$", m.group(2))
                        else f"1 {m.group(1)}{m.group(2)} {'is' if m.group(3) == 'are' else 'was'}", t)
    return t[:1].upper() + t[1:] if t else t


def _polish_finding(text: str) -> str:
    """
    "Review required: review required is recorded as true" -> "Review required:
    true". Only a repeat of the label itself and a leading "is/are recorded as"
    are removed from the value; the value's own words are never touched.
    """
    t = _polish_text(text).rstrip(".")
    label, sep, rest = t.partition(":")
    if not sep:
        return t
    rest = rest.strip()
    if rest.lower().startswith(label.strip().lower() + " "):
        rest = rest[len(label.strip()):].strip()
    rest = _COPULA.sub("", rest, count=1).strip() or rest
    label = label.strip()
    # A count is named by what it counts: "Failed transaction count: 25" ->
    # "Failed transactions: 25". Only for a plain number, only the label.
    m = re.fullmatch(r"(.*\S) count", label, re.IGNORECASE)
    if m and _NUMBER.fullmatch(rest):
        words = m.group(1).split(" ")
        w = words[-1]
        words[-1] = (w[:-1] + "ies" if re.search(r"[^aeiou]y$", w)
                     else w + "es" if re.search(r"(s|x|z|ch|sh)$", w) else w + "s")
        label = " ".join(words)
    # "Failed transactions: 25 failed transactions" -> "Failed transactions: 25":
    # only when the words after the number repeat the label exactly.
    n = re.fullmatch(r"(\S+)\s+(.+)", rest)
    if n and _NUMBER.fullmatch(n.group(1)) and n.group(2).lower() == label.lower():
        rest = n.group(1)
    return f"{label}: {rest}"


def polish(out: Any) -> Any:
    """The model's answer with its wording polished; anything malformed is returned as is."""
    try:
        return {
            **out,
            "summary_sentences": [{**it, "text": _polish_text(it["text"])} for it in out["summary_sentences"]],
            "key_findings": [{**it, "text": _polish_finding(it["text"])} for it in out["key_findings"]],
            "analyst_attention": [{**it, "text": _polish_text(it["text"])} for it in out["analyst_attention"]],
        }
    except (TypeError, KeyError, AttributeError):
        return out  # the shape checks report it


def _empty(status: str, model: str | None, reason: str) -> dict:
    return {
        "status": status,
        "generated_by": None,
        "model": model,
        "summary": None,
        "summary_evidence_keys": [],
        "summary_sentences": [],
        "key_findings": [],
        "missing_information": [],
        "analyst_attention": [],
        "reason": reason,
    }


class AIAnalyst:
    """
    Wraps one OpenAI client. `client` is anything with responses.create(), so
    tests pass a fake and never reach the network.
    """

    def __init__(self, client: Any | None, model: str | None, outcome_names: list[str],
                 unavailable_reason: str | None = None) -> None:
        self.client = client
        self.model = model
        self.outcome_names = list(outcome_names)
        self.unavailable_reason = unavailable_reason

    @classmethod
    def from_env(cls, outcome_names: list[str], environ: dict | None = None) -> "AIAnalyst":
        env = os.environ if environ is None else environ
        key = (env.get(API_KEY_ENV) or "").strip()
        model = ((env.get(MODEL_ENV) or "").strip()
                 or (env.get(LEGACY_MODEL_ENV) or "").strip() or None)
        if not key:
            return cls(None, model, outcome_names, f"{API_KEY_ENV} is not configured")
        if not model:
            return cls(None, None, outcome_names, f"{MODEL_ENV} is not configured")
        try:
            from openai import OpenAI
        except ImportError:
            return cls(None, model, outcome_names, "the openai package is not installed")
        client = OpenAI(api_key=key, timeout=REQUEST_TIMEOUT_SECONDS, max_retries=1)
        return cls(client, model, outcome_names)

    @property
    def available(self) -> bool:
        return self.client is not None

    def summarize(self, payload: dict) -> dict:
        """Never raises. Returns the summary dict described in the module docstring."""
        if not self.available:
            return _empty(STATUS_UNAVAILABLE, self.model,
                          f"AI summary unavailable: {self.unavailable_reason}")
        try:
            resp = self.client.responses.create(
                model=self.model,
                instructions=INSTRUCTIONS,
                input=json.dumps(payload, ensure_ascii=False),
                text={"format": {
                    "type": "json_schema",
                    "name": "analyst_summary",
                    "schema": output_schema(list(payload["evidence"])),
                    "strict": True,
                }},
            )
        except Exception as exc:  # any SDK, network or timeout failure
            log.warning("AI summary request failed: %s", type(exc).__name__)
            return _empty(STATUS_ERROR, self.model,
                          f"AI summary request failed ({type(exc).__name__})")

        status = getattr(resp, "status", "completed")
        if status != "completed":
            return _empty(STATUS_ERROR, self.model,
                          f"AI summary response was not completed (status: {status})")
        try:
            out = json.loads(getattr(resp, "output_text", "") or "")
        except (TypeError, ValueError):
            return _empty(STATUS_ERROR, self.model, "AI summary response was not valid JSON")

        out = polish(out)  # spelling, casing and agreement; checked below as polished
        problems = check_output(out, payload, self.outcome_names)
        if problems:
            log.warning("AI summary rejected: %s", problems)
            return _empty(STATUS_ERROR, self.model,
                          "AI summary rejected by evidence checks: " + "; ".join(problems[:5]))

        return assemble(out, payload, self.model)


def assemble(out: dict, payload: dict, model: str | None) -> dict:
    """
    The API form of an accepted answer. The sentences are joined into the
    `summary` paragraph and their citations into `summary_evidence_keys`, in
    order; the sentences about MISSING items are also `missing_information`.
    Nothing is reworded.
    """
    missing = {k for k, v in payload["evidence"].items() if v["status"] == MISSING}
    sentences = [{"text": it["text"].strip(), "evidence_keys": it["evidence_keys"]}
                 for it in out["summary_sentences"]]
    keys: list[str] = []
    for it in sentences:
        keys += [k for k in it["evidence_keys"] if k not in keys]
    return {
        "status": STATUS_GENERATED,
        "generated_by": "llm",
        "model": model,
        "summary": " ".join(it["text"] for it in sentences),
        "summary_evidence_keys": keys,
        "summary_sentences": sentences,
        "key_findings": out["key_findings"],
        "missing_information": [it for it in sentences if set(it["evidence_keys"]) & missing],
        # kind is a checking aid; the API contract stays {text, evidence_keys}.
        "analyst_attention": [{"text": it["text"], "evidence_keys": it["evidence_keys"]}
                              for it in out["analyst_attention"]],
        "reason": None,
    }
