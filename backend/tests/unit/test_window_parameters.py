"""
Window and lookback parameter behaviour — Phase 1 contract.

These tests PIN existing behaviour. They do not assert what the parameters
ought to do.

Established by the earlier audit and recorded as L1 in KNOWN_LIMITATIONS.md:
six tools accept a window or lookback argument; the argument is accepted,
participates in the deduplication key, and is echoed into the returned record
where the tool's contract declares it — but it filters nothing. The dataset
holds one precomputed summary per customer with no period attached, and no tool
receives the alert timestamp, so a window has no anchor to measure from.

Pinning this matters because the behaviour is invisible: a caller reading
tools.yaml sees "aggregate transaction activity over a window" and would
reasonably expect filtering. If someone later makes the parameter functional,
these tests fail loudly and deliberately — that is the signal to update them
together with the contract, not a regression.

Nothing here implements filtering, adds period fields, or changes production
behaviour.

Same plain-script style as the other suites. Exit code 0 on success.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from backend.paths import DATASET_PATH, KB_DIR  # noqa: E402
from backend.tools import DataStore  # noqa: E402

passed: list[str] = []
failed: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    (passed if cond else failed).append(f"{name}{(' — ' + detail) if detail else ''}")


# tool, table it reads, window argument name, extra args, field echoing the
# parameter back (None where the tool's contract does not declare an echo).
WINDOWED = [
    ("get_transaction_summary", "transaction_summaries", "window_hours", {}, "window_hours"),
    ("get_transactions", "transactions", "window_hours", {"limit": 5}, None),
    ("get_topup_summary", "topup_summaries", "window_hours", {}, "window_hours"),
    ("get_topups", "topups", "window_hours", {"limit": 5}, None),
    ("get_counterparty_summary", "counterparty_summaries", "window_hours", {}, "window_hours"),
    ("get_prior_alerts", "prior_alerts", "lookback_days", {}, "lookback_days"),
]

# Meaningfully different values: one short, one long, one between.
WINDOWS = {"window_hours": [24, 72, 168], "lookback_days": [30, 90, 180]}


def main() -> int:
    raw = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    digest_before = hashlib.md5(DATASET_PATH.read_bytes()).hexdigest()
    store = DataStore(DATASET_PATH)

    print("=" * 78)
    print("WINDOW / LOOKBACK PARAMETERS — Phase 1 advisory contract")
    print("=" * 78)

    # ------------------------------------------------- signature discovery
    print("\n" + "-" * 78)
    print("0. SIGNATURES — the set under test is derived, not hardcoded")
    print("-" * 78)
    discovered = []
    for name in sorted(n for n in dir(DataStore) if n.startswith("get_")):
        params = inspect.signature(getattr(DataStore, name)).parameters
        w = [p for p in params if p in ("window_hours", "lookback_days")]
        if w:
            discovered.append((name, w[0]))
    expected = {(n, a) for n, _, a, _, _ in WINDOWED}
    check("the six windowed tools are exactly those under test",
          set(discovered) == expected,
          f"discovered={sorted(discovered)} tested={sorted(expected)}")
    for n, a in discovered:
        print(f"  {n:<28}{a}")

    # ------------------------------------------------------------- Case 1
    print("\n" + "-" * 78)
    print("1. SAME REFERENCE, DIFFERENT WINDOWS — identical apart from the echo")
    print("-" * 78)
    for name, table, arg, extra, echo in WINDOWED:
        fn = getattr(store, name)
        refs = sorted(raw.get(table, {}))
        if not refs:
            check(f"{name}: table populated", False, f"{table} empty")
            continue
        ref = refs[0]
        values = WINDOWS[arg]
        results = {v: fn(**{"customer_ref": ref, arg: v}, **extra) for v in values}

        # strip the echoed parameter before comparing
        def stripped(d: dict) -> dict:
            return {k: v for k, v in d.items() if k != echo}

        base = stripped(results[values[0]])
        identical = all(stripped(results[v]) == base for v in values[1:])
        check(f"{name}: payload identical across {values}", identical,
              "payload differs between windows")
        check(f"{name}: payload is non-empty", base != {}, "nothing returned")

        if echo:
            echoed_ok = all(results[v].get(echo) == v for v in values)
            check(f"{name}: echoes the requested {arg} verbatim", echoed_ok,
                  str({v: results[v].get(echo) for v in values}))
            check(f"{name}: the echo is the ONLY difference between windows",
                  all(set(results[v]) == set(results[values[0]]) for v in values[1:])
                  and identical)
        else:
            check(f"{name}: does not echo {arg} (contract declares no echo)",
                  all(echo_key not in results[v] for v in values
                      for echo_key in ("window_hours", "lookback_days")),
                  str(sorted(results[values[0]])[:4]))
        print(f"  {name:<28}{arg}={values} -> payload identical, "
              f"echo={'yes' if echo else 'no'}")

    # ------------------------------------------------------------- Case 2
    print("\n" + "-" * 78)
    print("2. THE WINDOW DOES NOT FILTER THE UNDERLYING DATA")
    print("-" * 78)
    for name, table, arg, extra, echo in WINDOWED:
        fn = getattr(store, name)
        refs = sorted(raw.get(table, {}))
        if not refs:
            continue
        ref = refs[0]
        lo, hi = WINDOWS[arg][0], WINDOWS[arg][-1]
        r_lo = fn(**{"customer_ref": ref, arg: lo}, **extra)
        r_hi = fn(**{"customer_ref": ref, arg: hi}, **extra)
        source = raw[table][ref]

        if name in ("get_transactions", "get_topups"):
            # column-oriented: same number of rows at both windows, and that
            # count is driven by `limit`, never by the window
            lens_lo = {len(v) for v in r_lo.values()}
            lens_hi = {len(v) for v in r_hi.values()}
            check(f"{name}: row count unchanged by the window", lens_lo == lens_hi,
                  f"{lens_lo} vs {lens_hi}")
            check(f"{name}: row count is bounded by limit, not by the window",
                  lens_lo == {min(len(source), extra['limit'])},
                  f"{lens_lo} vs limit={extra['limit']} rows={len(source)}")
        else:
            payload_lo = {k: v for k, v in r_lo.items() if k != echo}
            check(f"{name}: every stored field is returned unfiltered at both windows",
                  payload_lo == {k: v for k, v in r_hi.items() if k != echo} == dict(source),
                  "returned payload differs from the stored record")
        print(f"  {name:<28}{arg}={lo} and {arg}={hi} return the same underlying data")

    # ------------------------------------------------------------- Case 3
    print("\n" + "-" * 78)
    print("3. REFERENCES STAY INDEPENDENT ACROSS WINDOWS")
    print("-" * 78)
    for name, table, arg, extra, echo in WINDOWED:
        fn = getattr(store, name)
        refs = sorted(raw.get(table, {}))
        if len(refs) < 2:
            check(f"{name}: two references available", False, f"only {len(refs)} in {table}")
            continue
        a, b = refs[0], refs[1]
        lo, hi = WINDOWS[arg][0], WINDOWS[arg][-1]
        a_lo = fn(**{"customer_ref": a, arg: lo}, **extra)
        a_hi = fn(**{"customer_ref": a, arg: hi}, **extra)
        b_lo = fn(**{"customer_ref": b, arg: lo}, **extra)

        # changing A's window must not make A look like B
        check(f"{name}: widening one reference's window does not pull in another's data",
              {k: v for k, v in a_hi.items() if k != echo}
              != {k: v for k, v in b_lo.items() if k != echo},
              f"{a} at {hi} became indistinguishable from {b} at {lo}")
        check(f"{name}: the two references are distinguishable at all",
              {k: v for k, v in a_lo.items() if k != echo}
              != {k: v for k, v in b_lo.items() if k != echo},
              f"{a} and {b} return identical payloads, so this assertion cannot discriminate")
        if name not in ("get_transactions", "get_topups"):
            check(f"{name}: each result carries its own reference",
                  a_hi.get("customer_ref") == a and b_lo.get("customer_ref") == b,
                  f"{a_hi.get('customer_ref')} / {b_lo.get('customer_ref')}")
        print(f"  {name:<28}{a} and {b} stay independent across {arg} {lo}->{hi}")

    # ------------------------------------------------------------- Case 4
    print("\n" + "-" * 78)
    print("4. THE CONTRACT DOCUMENT AND THE CODE AGREE ON WHICH TOOLS ARE AFFECTED")
    print("-" * 78)
    import yaml
    tools_yaml = yaml.safe_load((KB_DIR / "tools.yaml").read_text())["tools"]
    declared = {(n, a) for n, s in tools_yaml.items()
                for a in s["args"] if a in ("window_hours", "lookback_days")}
    check("tools.yaml declares the same windowed tools the code implements",
          declared == set(discovered), f"yaml={sorted(declared)} code={sorted(discovered)}")
    for name, _, arg, _, echo in WINDOWED:
        yaml_echo = arg in tools_yaml[name]["returns"]
        check(f"{name}: tools.yaml echo declaration matches the implementation",
              yaml_echo == (echo is not None),
              f"yaml declares echo={yaml_echo}, implementation echoes={echo is not None}")
    print(f"  {len(declared)} windowed tools declared in tools.yaml, matching the code")

    # ------------------------------------------------------------ integrity
    digest_after = hashlib.md5(DATASET_PATH.read_bytes()).hexdigest()
    check("dataset file untouched", digest_before == digest_after)

    print("\n" + "=" * 78)
    for p in passed:
        print(f"  PASS  {p}")
    for f in failed:
        print(f"  FAIL  {f}")
    print("=" * 78)
    print(f"{len(passed)} passed, {len(failed)} failed")
    if not failed:
        print("\nPinned: window and lookback arguments are accepted and echoed where the")
        print("contract declares an echo, but they do not filter Phase 1 data (L1).")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
