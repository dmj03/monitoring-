"""
Direct unit tests for the ten DataStore tools.

These exist because the integration suites exercise the tools only through
InvestigationEngine. The contract the whole MISSING path rests on — an absent
subject yields {} rather than raising — was previously proven only
incidentally, by alerts that happened to have gaps.

Valid-reference expectations are derived from the canonical dataset at runtime
rather than hardcoded, so these tests verify the tool's lookup behaviour rather
than restating fixture content.

Behaviour asserted here was observed from the current implementation, not
assumed. Nothing in production code was changed to make a test pass.

Same plain-script style as the existing suites: no new framework, no new
dependency. Exit code 0 on success.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from backend.paths import DATASET_PATH  # noqa: E402
from backend.tools import DataStore  # noqa: E402

passed: list[str] = []
failed: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    (passed if cond else failed).append(f"{name}{(' — ' + detail) if detail else ''}")


# Each tool, the table it reads, the name of its reference argument, and any
# additional arguments the contract requires.
TOOLS = [
    ("get_customer", "customers", "customer_ref", {}),
    ("get_programme", "programmes", "programme_ref", {}),
    ("get_transaction_summary", "transaction_summaries", "customer_ref", {"window_hours": 48}),
    ("get_transactions", "transactions", "customer_ref", {"window_hours": 48, "limit": 5}),
    ("get_topup_summary", "topup_summaries", "customer_ref", {"window_hours": 72}),
    ("get_topups", "topups", "customer_ref", {"window_hours": 72, "limit": 5}),
    ("get_screening", "screenings", "customer_ref", {}),
    ("get_kyb_history", "kyb_history", "customer_ref", {}),
    ("get_counterparty_summary", "counterparty_summaries", "customer_ref", {"window_hours": 168}),
    ("get_prior_alerts", "prior_alerts", "customer_ref", {"lookback_days": 90}),
]

# Tools that inject the call parameter into the returned record.
ECHOES = {"get_transaction_summary": "window_hours", "get_topup_summary": "window_hours",
          "get_counterparty_summary": "window_hours", "get_prior_alerts": "lookback_days"}

# Tools returning a column-oriented view of individual records rather than one
# flat record, so their shape is compared differently.
ROW_TOOLS = {"get_transactions", "get_topups"}


def main() -> int:
    raw = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    store = DataStore(DATASET_PATH)

    print("=" * 78)
    print("DATASTORE UNIT TESTS")
    print("=" * 78)
    print(f"\ndataset: {DATASET_PATH}")
    print(f"{'tool':<28}{'table':<24}{'refs in table':>14}")
    print("-" * 78)
    for name, table, _, _ in TOOLS:
        print(f"  {name:<26}{table:<24}{len(raw.get(table, {})):>14}")

    # ---------------------------------------------------------------- 1
    print("\n" + "-" * 78)
    print("1. VALID REFERENCE — expectation derived from the dataset")
    print("-" * 78)
    for name, table, ref_arg, extra in TOOLS:
        fn = getattr(store, name)
        refs = sorted(raw.get(table, {}))
        if not refs:
            check(f"{name}: table populated", False, f"{table} empty")
            continue
        ref = refs[0]
        source = raw[table][ref]
        try:
            out = fn(**{ref_arg: ref}, **extra)
            raised = None
        except Exception as e:  # noqa: BLE001
            out, raised = None, e
        check(f"{name}: valid ref does not raise", raised is None, repr(raised))
        if raised is not None:
            continue
        check(f"{name}: valid ref returns a dict", isinstance(out, dict), type(out).__name__)
        check(f"{name}: valid ref returns a non-empty record", out != {})

        if name in ROW_TOOLS:
            # column-oriented: every declared key of the first row is present,
            # and each column holds at most `limit` values in source order
            limit = extra["limit"]
            expected_rows = source[:limit]
            ok_keys = set(out) == set(expected_rows[0])
            ok_vals = all(out[k] == [r.get(k) for r in expected_rows] for k in out)
            check(f"{name}: columns match the source rows", ok_keys and ok_vals,
                  f"keys={ok_keys} values={ok_vals}")
            check(f"{name}: respects limit", all(len(v) <= limit for v in out.values()),
                  f"limit={limit}")
        else:
            echoed = ECHOES.get(name)
            expected = dict(source)
            if echoed:
                expected[echoed] = extra[echoed]
            check(f"{name}: record matches the source record exactly", out == expected,
                  f"diff keys={sorted(set(out) ^ set(expected))}")
            if echoed:
                check(f"{name}: echoes {echoed} into the record",
                      out.get(echoed) == extra[echoed], str(out.get(echoed)))
        print(f"  {name:<28}ref={ref:<12}fields={len(out):<4}OK")

    # ---------------------------------------------------------------- 2-4
    BAD = [("absent reference", "NO-SUCH-REF-0000"),
           ("empty-string reference", ""),
           ("malformed reference", "INVALID")]
    for label, bad in BAD:
        print("\n" + "-" * 78)
        print(f"{BAD.index((label, bad)) + 2}. {label.upper()} — expect {{}} and no exception")
        print("-" * 78)
        for name, _, ref_arg, extra in TOOLS:
            fn = getattr(store, name)
            try:
                out = fn(**{ref_arg: bad}, **extra)
                raised = None
            except Exception as e:  # noqa: BLE001
                out, raised = None, e
            check(f"{name}: {label} does not raise", raised is None, repr(raised))
            check(f"{name}: {label} returns {{}}", out == {}, repr(out)[:60])
            # an empty result must not carry the echoed call parameter either
            if raised is None and name in ECHOES:
                check(f"{name}: {label} does not echo {ECHOES[name]}",
                      ECHOES[name] not in (out or {}))
        print(f"  all 10 tools returned {{}} without raising for {bad!r}")

    # ---------------------------------------------------------------- 5
    print("\n" + "-" * 78)
    print("5. DISTINCTNESS — two different valid references never share a record")
    print("-" * 78)
    for name, table, ref_arg, extra in TOOLS:
        fn = getattr(store, name)
        refs = sorted(raw.get(table, {}))
        if len(refs) < 2:
            check(f"{name}: two distinct refs available", False, f"only {len(refs)} in {table}")
            continue
        a, b = refs[0], refs[1]
        out_a = fn(**{ref_arg: a}, **extra)
        out_b = fn(**{ref_arg: b}, **extra)
        check(f"{name}: distinct refs give distinct records", out_a != out_b,
              f"{a} and {b} returned identical records")
        # and each result must correspond to the reference that was asked for
        if name not in ROW_TOOLS and ref_arg in out_a:
            check(f"{name}: record carries the requested reference",
                  out_a[ref_arg] == a and out_b[ref_arg] == b,
                  f"{out_a.get(ref_arg)} / {out_b.get(ref_arg)}")
        print(f"  {name:<28}{a} != {b}  OK")

    # ---------------------------------------------------------------- 6
    print("\n" + "-" * 78)
    print("6. ALIASING — documents the current contract, which is not uniform")
    print("-" * 78)
    # Observed, not assumed: tools returning _record() directly hand back the
    # stored object itself; those wrapping it in dict() hand back a copy.
    # Asserted here as-is. Mutating a returned record from an aliasing tool
    # therefore changes the in-memory store. It cannot reach disk — DataStore
    # has no write path — but a caller holding a record must not mutate it.
    ALIASING = {"get_customer", "get_programme", "get_screening", "get_kyb_history"}
    probe = DataStore(DATASET_PATH)  # throwaway store, never the shared one
    for name, table, ref_arg, extra in TOOLS:
        refs = sorted(raw.get(table, {}))
        if not refs:
            continue
        out = getattr(probe, name)(**{ref_arg: refs[0]}, **extra)
        stored = probe.d[table][refs[0]]
        is_alias = out is stored
        expected_alias = name in ALIASING
        check(f"{name}: aliasing matches the documented contract",
              is_alias == expected_alias,
              f"returns stored object={is_alias}, documented={expected_alias}")
        print(f"  {name:<28}{'returns stored object' if is_alias else 'returns a copy'}")

    # Whatever the aliasing, no tool may reach the file on disk.
    digest_before = hashlib.md5(DATASET_PATH.read_bytes()).hexdigest()
    sink = DataStore(DATASET_PATH)
    for name, table, ref_arg, extra in TOOLS:
        refs = sorted(raw.get(table, {}))
        if refs:
            r = getattr(sink, name)(**{ref_arg: refs[0]}, **extra)
            if isinstance(r, dict):
                r["__probe__"] = 1
    digest_after = hashlib.md5(DATASET_PATH.read_bytes()).hexdigest()
    check("no tool can reach the dataset file", digest_before == digest_after,
          f"{digest_before[:8]} -> {digest_after[:8]}")
    check("the shared store used by these tests is unpolluted",
          not any("__probe__" in v for v in store.d["customers"].values()))
    print(f"  dataset file md5 unchanged after mutating every returned record: "
          f"{digest_before == digest_after}")

    # ---------------------------------------------------------------- report
    print("\n" + "=" * 78)
    for p in passed:
        print(f"  PASS  {p}")
    for f in failed:
        print(f"  FAIL  {f}")
    print("=" * 78)
    print(f"{len(passed)} passed, {len(failed)} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
