"""
Investigation engine. Moved unchanged from engine.py.

Routes an alert to its playbook, plans and deduplicates the tool calls,
executes them through the tools layer, and assembles the evidence pack.

Depends on the tools layer. Does not depend on clustering.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from backend.tools.datastore import MISSING, DataStore


@dataclass
class EvidenceItem:
    key: str
    status: str  # FOUND | MISSING
    value: Any
    tool: str
    field: str
    args: dict


@dataclass
class EvidencePack:
    alert_id: str
    rule_id: str
    category: str
    playbook_id: str
    evidence: list[EvidenceItem] = field(default_factory=list)
    tool_calls: list[dict] = field(default_factory=list)
    checklist: list[str] = field(default_factory=list)
    investigation_focus: str = ""
    escalation_guidance: str = ""
    ai_boundaries: str = ""
    global_boundaries: dict = field(default_factory=dict)
    allowed_outcomes: list[str] = field(default_factory=list)
    cluster_ref: str | None = None

    @property
    def missing(self) -> list[str]:
        return [e.key for e in self.evidence if e.status == MISSING]

    @property
    def found(self) -> list[str]:
        return [e.key for e in self.evidence if e.status == "FOUND"]

    def to_dict(self) -> dict:
        return {
            "alert_id": self.alert_id,
            "rule_id": self.rule_id,
            "category": self.category,
            "playbook_id": self.playbook_id,
            "cluster_ref": self.cluster_ref,
            "tool_call_count": len(self.tool_calls),
            "evidence": [
                {"key": e.key, "status": e.status, "value": e.value, "source": f"{e.tool}.{e.field}"}
                for e in self.evidence
            ],
            "missing_evidence": self.missing,
            "investigation_focus": self.investigation_focus,
            "checklist": self.checklist,
            "escalation_guidance": self.escalation_guidance,
            "ai_boundaries": self.ai_boundaries,
        }


class InvestigationEngine:
    def __init__(self, kb_dir: Path, store: DataStore) -> None:
        self.tools_doc = yaml.safe_load((kb_dir / "tools.yaml").read_text())
        self.playbooks = yaml.safe_load((kb_dir / "playbooks.yaml").read_text())["playbooks"]
        cfg = yaml.safe_load((kb_dir / "config.yaml").read_text())
        self.routing = cfg["routing"]
        self.outcomes = cfg["investigation_outcomes"]
        pb_doc = yaml.safe_load((kb_dir / "playbooks.yaml").read_text())
        self.global_boundaries = pb_doc.get("global_ai_boundaries", {})
        self.store = store

    def _resolve(self, src: Any, alert: dict) -> Any:
        if isinstance(src, str) and src.startswith("alert."):
            return alert[src.split(".", 1)[1]]
        return src

    def investigate(self, alert: dict) -> EvidencePack:
        rule_id = alert["rule_id"]
        pb_id = self.routing[rule_id]
        pb = self.playbooks[pb_id]

        pack = EvidencePack(
            alert_id=alert["alert_id"],
            rule_id=rule_id,
            category=pb["category"],
            playbook_id=pb_id,
            checklist=pb.get("investigation_checklist", []),
            investigation_focus=pb.get("investigation_focus", "").strip(),
            escalation_guidance=pb.get("escalation_guidance", "").strip(),
            ai_boundaries=pb.get("ai_boundaries", "").strip(),
            global_boundaries=self.global_boundaries,
            allowed_outcomes=pb.get("allowed_outcomes", self.outcomes),
            cluster_ref=alert.get("cluster_ref"),
        )

        # Plan: one call per distinct (tool, resolved args).
        plan: dict[tuple, dict] = {}
        for item in pb["required_evidence"]:
            args = {k: self._resolve(v, alert) for k, v in item.get("args", {}).items()}
            sig = (item["tool"], tuple(sorted(args.items())))
            plan.setdefault(sig, args)

        # Execute once per distinct call.
        results: dict[tuple, dict] = {}
        for sig, args in plan.items():
            tool_name = sig[0]
            fn = getattr(self.store, tool_name)
            results[sig] = fn(**args)
            pack.tool_calls.append({"tool": tool_name, "args": args})

        # Map evidence keys onto the fetched records.
        for item in pb["required_evidence"]:
            args = {k: self._resolve(v, alert) for k, v in item.get("args", {}).items()}
            sig = (item["tool"], tuple(sorted(args.items())))
            rec = results[sig]
            raw = rec.get(item["field"], None)
            status = MISSING if raw in (None, [], "", {}) else "FOUND"
            pack.evidence.append(
                EvidenceItem(
                    key=item["key"],
                    status=status,
                    value=None if status == MISSING else raw,
                    tool=item["tool"],
                    field=item["field"],
                    args=args,
                )
            )
        return pack

    def build_llm_input(self, pack: EvidencePack) -> dict:
        """
        Everything a model may be given about one investigation. Nothing outside
        this dict is in scope for it.

        Each evidence item keeps its provenance (tool, field, resolved args) so a
        model's statement can be traced back to the call that produced it.

        The AI analyst summary (backend/ai/analyst.py) sends a subset of this:
        it does not send permitted_outcomes, escalation_guidance or the
        checklist, because that feature explains evidence and never recommends.
        """
        alert = {"alert_id": pack.alert_id, "rule_id": pack.rule_id, "category": pack.category}
        if pack.cluster_ref:
            alert["cluster_ref"] = pack.cluster_ref
        return {
            "alert": alert,
            "playbook_id": pack.playbook_id,
            "investigation_focus": pack.investigation_focus,
            "checklist": pack.checklist,
            "escalation_guidance": pack.escalation_guidance,
            "global_boundaries": pack.global_boundaries,
            "boundaries": pack.ai_boundaries,
            "evidence": {
                e.key: {
                    "status": e.status,
                    "value": e.value if e.status == "FOUND" else None,
                    "tool": e.tool,
                    "field": e.field,
                    "args": e.args,
                }
                for e in pack.evidence
            },
            "missing_evidence": pack.missing,
            "permitted_outcomes": pack.allowed_outcomes,
            "instruction": (
                "Explain the evidence above only. Do not introduce facts not present. "
                "Mark the output AI-generated. A MISSING item means the information "
                "was not available."
            ),
        }
