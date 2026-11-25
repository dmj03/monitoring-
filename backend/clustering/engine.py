"""
Cluster engine. Moved unchanged from engine.py.

Groups alerts using alert-level fields and the configured thresholds. Does not
read enrichment data and does not depend on the investigation layer.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml


@dataclass
class Cluster:
    cluster_ref: str
    cluster_type: str
    label: str
    cluster_category: str
    alert_ids: list[str]
    customer_refs: list[str]
    programme_refs: list[str]
    rule_ids: list[str]
    time_span_hours: float
    thresholds: dict


class ClusterEngine:
    """Alert-level matching only. Never reads enrichment data."""

    def __init__(self, kb_dir: Path) -> None:
        cfg = yaml.safe_load((kb_dir / "config.yaml").read_text())
        self.clusters = cfg["clusters"]
        self.order = cfg["cluster_evaluation_order"]

    @staticmethod
    def _ts(a: dict) -> datetime:
        return datetime.fromisoformat(a["created_at"].replace("Z", "+00:00"))

    def detect(self, alerts: list[dict], overrides: dict | None = None) -> tuple[list[Cluster], dict[str, str]]:
        overrides = overrides or {}
        claimed: dict[str, str] = {}
        out: list[Cluster] = []
        seq = 0

        for ctype in self.order:
            cdef = self.clusters[ctype]
            th = {**cdef["thresholds"], **overrides.get(ctype, {})}
            rule_ids = set(cdef["match_on"].get("rule_ids", []))
            group_by = cdef["match_on"].get("group_by", [])

            buckets: dict[Any, list[dict]] = defaultdict(list)
            for a in alerts:
                if a["alert_id"] in claimed:
                    continue  # one alert, one cluster
                if rule_ids and a["rule_id"] not in rule_ids:
                    continue
                key = tuple(a.get(g) for g in group_by)
                if any(k is None for k in key):
                    continue
                buckets[key].append(a)

            for key, members in buckets.items():
                members = sorted(members, key=self._ts)
                window = th["time_window_hours"]
                # Sliding window over the sorted members.
                best: list[dict] = []
                for i in range(len(members)):
                    j = i
                    while j < len(members) and (self._ts(members[j]) - self._ts(members[i])).total_seconds() / 3600 <= window:
                        j += 1
                    if j - i > len(best):
                        best = members[i:j]
                if len(best) < th["min_alerts"]:
                    continue
                customers = sorted({m["customer_ref"] for m in best})
                if len(customers) < th.get("min_customers", 0):
                    continue

                seq += 1
                cl = Cluster(
                    cluster_ref=f"C{seq:03d}",
                    cluster_type=ctype,
                    label=cdef["label"],
                    cluster_category=cdef["cluster_category"],
                    alert_ids=[m["alert_id"] for m in best],
                    customer_refs=customers,
                    programme_refs=sorted({m["programme_ref"] for m in best}),
                    rule_ids=sorted({m["rule_id"] for m in best}),
                    time_span_hours=round((self._ts(best[-1]) - self._ts(best[0])).total_seconds() / 3600, 1),
                    thresholds=th,
                )
                out.append(cl)
                for m in best:
                    claimed[m["alert_id"]] = cl.cluster_ref

        return out, claimed
