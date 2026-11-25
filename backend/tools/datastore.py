"""
Data access. Moved unchanged from engine.py.

The only way the investigation layer reads the dataset. Read-only: an absent
subject or an absent field yields MISSING rather than an error, and nothing
here writes.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

MISSING = "MISSING"


class DuplicateAlertError(Exception):
    """Raised when an alert_id already present in the dataset is submitted again."""


class DataStore:
    """The mock dataset. Tools are the only way the engine reads it."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.d = json.loads(self.path.read_text(encoding="utf-8"))

    # Each tool returns a flat record. An absent subject or an absent field
    # yields MISSING rather than raising: a gap is data, not an error.

    def _record(self, table: str, key: str) -> dict:
        return self.d.get(table, {}).get(key) or {}

    def get_customer(self, customer_ref: str) -> dict:
        return self._record("customers", customer_ref)

    def get_programme(self, programme_ref: str) -> dict:
        return self._record("programmes", programme_ref)

    def get_transaction_summary(self, customer_ref: str, window_hours: int) -> dict:
        rec = dict(self._record("transaction_summaries", customer_ref))
        if rec:
            rec["window_hours"] = window_hours
        return rec

    def get_transactions(self, customer_ref: str, window_hours: int, limit: int) -> dict:
        rows = self.d.get("transactions", {}).get(customer_ref, [])[:limit]
        if not rows:
            return {}
        return {k: [r.get(k) for r in rows] for k in rows[0]}

    def get_topup_summary(self, customer_ref: str, window_hours: int) -> dict:
        rec = dict(self._record("topup_summaries", customer_ref))
        if rec:
            rec["window_hours"] = window_hours
        return rec

    def get_topups(self, customer_ref: str, window_hours: int, limit: int) -> dict:
        rows = self.d.get("topups", {}).get(customer_ref, [])[:limit]
        if not rows:
            return {}
        return {k: [r.get(k) for r in rows] for k in rows[0]}

    def get_screening(self, customer_ref: str) -> dict:
        return self._record("screenings", customer_ref)

    def get_kyb_history(self, customer_ref: str) -> dict:
        return self._record("kyb_history", customer_ref)

    def get_counterparty_summary(self, customer_ref: str, window_hours: int) -> dict:
        rec = dict(self._record("counterparty_summaries", customer_ref))
        if rec:
            rec["window_hours"] = window_hours
        return rec

    def get_prior_alerts(self, customer_ref: str, lookback_days: int) -> dict:
        rec = dict(self._record("prior_alerts", customer_ref))
        if rec:
            rec["lookback_days"] = lookback_days
        return rec

    @property
    def alerts(self) -> list[dict]:
        return self.d["alerts"]

    # ---------------------------------------------------------------- write
    # The one write operation. Everything above stays read-only.
    #
    # The file on disk is the source of truth, so the sequence re-reads it
    # rather than trusting the in-memory copy, which may be stale if another
    # process appended in the meantime. The write itself is atomic: content
    # goes to a temporary file in the same directory, is flushed and fsynced,
    # then os.replace() swaps it in. A failure therefore leaves the original
    # file intact rather than half-written, and no temporary file is left
    # behind on either path.
    #
    # indent=1 matches how the dataset was generated, so an append leaves every
    # other byte of the file unchanged.

    def add_alert(self, alert: dict) -> dict:
        """
        Append one alert to the dataset and persist it.

        Raises DuplicateAlertError if alert_id is already present. Returns the
        stored alert. Only the `alerts` list is touched: no other table, and no
        existing alert, is modified.
        """
        current = json.loads(self.path.read_text(encoding="utf-8"))

        if any(a["alert_id"] == alert["alert_id"] for a in current["alerts"]):
            raise DuplicateAlertError(alert["alert_id"])

        current["alerts"].append(dict(alert))

        tmp_fd, tmp_name = tempfile.mkstemp(
            dir=str(self.path.parent), prefix=f".{self.path.name}.", suffix=".tmp")
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
                json.dump(current, fh, indent=1)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_name, self.path)
        except BaseException:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
            raise

        # Refresh from what was actually written, so memory and disk agree.
        self.d = json.loads(self.path.read_text(encoding="utf-8"))
        return self.d["alerts"][-1]
