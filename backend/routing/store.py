"""
Routing store: the analyst's routing decisions, for alerts and for clusters.

Operational analyst state, kept apart from the generated alert data. This module
never reads or writes data/dataset.json; the alert dataset stays immutable source
data and the alert record carries no routing field.

A decision is either a destination (where it is sent) or an outcome (closed
without routing). Destinations are workflow configuration, not knowledge-base
procedure, so they are defined here. The one outcome offered is the knowledge
base's own outcome name, so whether a playbook permits it is decided by the
caller from the playbook's allowed_outcomes.

Two kinds of record, one file:

  alerts    alert_id -> decision made on that alert
  clusters  "<cluster_type>:<sorted member alert_ids>" -> decision made on a
            cluster, with the exact member list it was made for. cluster_ref is
            a per-run reference and is never used as a key. A cluster whose
            membership has changed has a different key, so it is unrouted: an
            old decision is never applied to a new membership.

Every decision gets the next value of a file-wide sequence. An alert's effective
routing is whichever applies to it with the higher sequence: its own record, or
a cluster record listing it. So routing a cluster takes precedence over earlier
individual decisions without deleting them, and a later individual decision
takes precedence again for that alert only.

Writes are atomic, like DataStore.add_alert: the file is re-read, the change
applied, written to a temporary file in the same directory, fsynced, and swapped
in with os.replace. A failure leaves the previous file intact. A file written by
the first version of this store (a flat alert_id -> decision map) is read as
alert records and rewritten in this shape on the next save.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

# Where an alert or cluster can be sent. id -> (label, one-line meaning).
DESTINATIONS: dict[str, tuple[str, str]] = {
    "mlro": ("MLRO / Nominated Officer", "AML suspicion requiring specialist judgement"),
    "sanctions_analyst": ("Sanctions Analyst", "Screening/PEP result requiring manual review"),
    "monitoring_engineering": ("Monitoring Engineering", "Monitoring rule/pattern appears mistuned"),
    "programme_operations": ("Programme Operations", "Technical or programme/partner-specific issue"),
    "kyb_cdd": ("KYB / CDD Team", "Incomplete customer/KYB information"),
}

# Outcomes that close without routing. Keys are knowledge-base outcome names
# (config.yaml investigation_outcomes).
OUTCOMES: dict[str, tuple[str, str]] = {
    "no_action_required": ("No Further Action", "Close the alert without routing it"),
}


def cluster_key(cluster_type: str, alert_ids: list[str]) -> str:
    """Durable identity of a cluster decision: its type plus its exact members."""
    return f"{cluster_type}:{','.join(sorted(alert_ids))}"


class RoutingStore:
    """data/routing.json: {"seq": n, "alerts": {...}, "clusters": {...}}."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    # ------------------------------------------------------------------ read
    def _load(self) -> dict:
        if not self.path.exists():
            return {"seq": 0, "alerts": {}, "clusters": {}}
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        if "alerts" in raw or "clusters" in raw:
            return {"seq": raw.get("seq", 0), "alerts": raw.get("alerts", {}),
                    "clusters": raw.get("clusters", {})}
        # First-version file: a flat alert_id -> decision map. Its decisions
        # predate any sequence, so they rank below every newer decision.
        return {"seq": 0, "alerts": {k: {**v, "seq": 0} for k, v in raw.items()}, "clusters": {}}

    def alerts(self) -> dict[str, dict]:
        """Decisions made on individual alerts, as stored."""
        return self._load()["alerts"]

    def clusters(self) -> dict[str, dict]:
        """Decisions made on clusters, keyed by cluster_key()."""
        return self._load()["clusters"]

    def effective(self) -> dict[str, dict]:
        """
        alert_id -> the decision in force for that alert: its own record or a
        cluster record listing it, whichever is newer. Cluster-derived entries
        are marked source "cluster" with the cluster's label and type.
        """
        data = self._load()
        out: dict[str, dict] = {
            a: {**r, "source": "alert", "cluster_type": None, "cluster_label": None}
            for a, r in data["alerts"].items()}
        for c in sorted(data["clusters"].values(), key=lambda r: r.get("seq", 0)):
            for a in c["alert_ids"]:
                mine = out.get(a)
                if mine is None or c.get("seq", 0) > mine.get("seq", 0):
                    out[a] = {"alert_id": a, "destination": c["destination"], "outcome": c["outcome"],
                              "routed_at": c["routed_at"], "seq": c.get("seq", 0), "source": "cluster",
                              "cluster_type": c["cluster_type"], "cluster_label": c["label"]}
        return out

    # ----------------------------------------------------------------- write
    def set_alert(self, record: dict) -> dict:
        """Store one alert's decision, replacing that alert's earlier one."""
        data = self._load()
        data["seq"] += 1
        data["alerts"][record["alert_id"]] = {**record, "seq": data["seq"]}
        self._write(data)
        return data["alerts"][record["alert_id"]]

    def set_cluster(self, record: dict) -> dict:
        """Store a cluster's decision for its exact membership. Member alert records are kept."""
        data = self._load()
        data["seq"] += 1
        key = cluster_key(record["cluster_type"], record["alert_ids"])
        data["clusters"][key] = {**record, "key": key, "alert_ids": sorted(record["alert_ids"]),
                                 "seq": data["seq"]}
        self._write(data)
        return data["clusters"][key]

    def _write(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_fd, tmp_name = tempfile.mkstemp(
            dir=str(self.path.parent), prefix=f".{self.path.name}.", suffix=".tmp")
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=1, sort_keys=True)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_name, self.path)
        except BaseException:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
            raise
