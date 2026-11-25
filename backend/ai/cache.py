"""
Cache of accepted AI summaries, alert and cluster alike.

Only summaries that passed every grounding check are stored. Unavailable,
failed, timed-out and rejected results never are, so they are always retried.

An entry is keyed by fingerprint(): a SHA-256 of the summary kind, the prompt
text, the output schema, the model name and the exact model input. Any change
to the evidence or cluster data, the prompt, the schema or OPENAI_MODEL gives a
different key, so a cached result can never be served for different input.

Callers re-run the current grounding checks on a cached output before using it
(see cached_or_generate). If the checks have become stricter since the entry was
written, the entry is treated as a miss and regenerated: the cache can never
serve an answer today's checks would reject.

Persisted in its own file (data/ai_cache.json by default), apart from
dataset.json and routing.json, with the same atomic write: re-read, change,
write to a temporary file in the same directory, fsync, os.replace. A lock
serialises writers within the process.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


def fingerprint(kind: str, instructions: str, schema: dict, model: str | None, payload: dict) -> str:
    """Identity of one model request: everything that can change the answer."""
    blob = json.dumps({"kind": kind, "instructions": instructions, "schema": schema,
                       "model": model, "input": payload}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def prompt_version(instructions: str, schema: dict) -> str:
    """Short, human-readable identity of the prompt and schema, stored with an entry."""
    return hashlib.sha256((instructions + json.dumps(schema, sort_keys=True)).encode()).hexdigest()[:12]


class AICache:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()

    def _load(self) -> dict:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}  # an unreadable cache is an empty cache, never an error

    def get(self, key: str) -> dict | None:
        return self._load().get(key)

    def put(self, key: str, entry: dict) -> None:
        with self._lock:
            data = self._load()
            data[key] = entry
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), prefix=f".{self.path.name}.", suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump(data, fh, indent=1, sort_keys=True)
                    fh.flush()
                    os.fsync(fh.fileno())
                os.replace(tmp, self.path)
            except BaseException:
                if os.path.exists(tmp):
                    os.unlink(tmp)
                raise

    def clear(self) -> None:
        with self._lock:
            if self.path.exists():
                self.path.unlink()


def cached_or_generate(
    cache: AICache,
    *,
    kind: str,
    key: str,
    version: str,
    regenerate: bool,
    output_fields: tuple[str, ...],
    recheck: Callable[[dict], list[str]],
    generate: Callable[[], dict],
) -> dict:
    """
    The summary for this exact request, from the cache when possible.

    - Normal request: a cached entry whose output still passes recheck() is
      returned with cached=True and no model call.
    - Otherwise, or with regenerate=True, generate() is called. Only a result with
      status "generated" is stored, replacing any earlier entry for the key; any
      other result is returned as is and the cache is left untouched.
    """
    if not regenerate:
        entry = cache.get(key)
        if entry and not recheck(entry["output"]):
            return {**entry["result"], "cached": True, "generated_at": entry["generated_at"]}

    result = generate()
    if result.get("status") != "generated":
        return {**result, "cached": False, "generated_at": None}

    now = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    output: dict[str, Any] = {k: result[k] for k in output_fields}
    try:
        cache.put(key, {"kind": kind, "model": result.get("model"), "prompt_version": version,
                        "generated_at": now, "output": output, "result": result})
    except OSError:
        pass  # failing to cache never fails the summary
    return {**result, "cached": False, "generated_at": now}
