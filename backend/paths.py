"""
Project-relative path resolution.

Exists so no caller has to hardcode a machine-specific absolute path. The
engines still take kb_dir as a constructor argument; these are defaults.
"""

from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
KB_DIR = PROJECT_ROOT / "UC3_Monitoring_KB"
DATASET_PATH = PROJECT_ROOT / "data" / "dataset.json"
