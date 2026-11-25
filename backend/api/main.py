"""
FastAPI application.

Lifecycle decision, based on the Phase 1 inspection rather than convention:

  DataStore, InvestigationEngine and ClusterEngine are constructed ONCE at
  startup and shared across requests.

  Justification, measured not assumed:
    - investigate() over 30 alerts mutates neither the store nor the engine
    - detect() mutates neither the cluster configuration nor its own state,
      including when called with threshold overrides
    - detect() is idempotent across repeated calls
    - the existing test_read_only.py suite proves 180 investigations and 4
      clustering runs leave every project file byte-identical

  One caveat carried from test_datastore.py: four DataStore tools return the
  stored dict object itself rather than a copy, so a caller that MUTATED a
  returned record would pollute the shared store. The API layer never mutates
  returned records — routes.py only reads them into response models — so the
  shared instance is safe here. This is recorded rather than worked around,
  because fixing it belongs to the tools layer, not the API.

The app holds the engines on app.state and hands them to routes through
dependency functions, so no module-level mutable globals are introduced and the
engines remain constructible and usable without FastAPI.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

import yaml
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from backend.ai import AIAnalyst
from backend.ai.cache import AICache
from backend.api.routes import router
from backend.clustering import ClusterEngine
from backend.investigation import InvestigationEngine
from backend.paths import DATASET_PATH, KB_DIR, PROJECT_ROOT
from backend.routing import RoutingStore
from backend.tools import DataStore

log = logging.getLogger("uc3.api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # The dataset path may be overridden so a test (or a separate deployment)
    # can run against a copy without touching the canonical file. Default is
    # the canonical dataset; nothing else about startup changes.
    dataset_path = Path(os.environ.get("UC3_DATASET_PATH", DATASET_PATH))
    store = DataStore(dataset_path)
    app.state.datastore = store
    app.state.investigation_engine = InvestigationEngine(KB_DIR, store)
    app.state.cluster_engine = ClusterEngine(KB_DIR)
    # Analyst routing decisions live in their own file beside whichever dataset
    # is in use, so a test pointed at a dataset copy also gets its own routing
    # file. Routing never writes the dataset itself.
    # KB cluster purpose and boundaries, given to the AI cluster summary.
    # Mechanics stay in config.yaml; analyst_guidance is not loaded here.
    app.state.cluster_guidance = {
        f.stem: {k: v for k, v in yaml.safe_load(f.read_text(encoding="utf-8")).items()
                 if k in ("purpose", "boundaries")}
        for f in sorted((KB_DIR / "clusters").glob("*.yaml"))}
    app.state.routing_store = RoutingStore(
        Path(os.environ.get("UC3_ROUTING_PATH", dataset_path.parent / "routing.json")))

    pb_doc = yaml.safe_load((KB_DIR / "playbooks.yaml").read_text())
    cfg = yaml.safe_load((KB_DIR / "config.yaml").read_text())
    app.state.dataset_path = dataset_path
    app.state.kb_version = pb_doc["version"]
    app.state.playbook_count = len(pb_doc["playbooks"])
    app.state.cluster_type_count = len(cfg["clusters"])

    # The AI analyst reads OPENAI_API_KEY and OPENAI_MODEL. A project-root .env
    # is loaded if present; real environment variables take precedence. With no
    # key the analyst reports "unavailable" and everything else works as before.
    try:
        from dotenv import load_dotenv
        load_dotenv(PROJECT_ROOT / ".env", override=False)
    except ImportError:
        pass
    app.state.ai_analyst = AIAnalyst.from_env(outcome_names=cfg["investigation_outcomes"])
    # Adding alerts (POST /alerts) is frozen unless UC3_ALERT_CREATION is switched
    # on, in the environment or .env. /health reports it so the UI can follow.
    app.state.alert_creation_enabled = (
        os.environ.get("UC3_ALERT_CREATION", "").strip().lower() in ("1", "true", "yes", "on"))
    # Accepted AI summaries, cached in their own file beside the dataset in use.
    app.state.ai_cache = AICache(
        Path(os.environ.get("UC3_AI_CACHE_PATH", dataset_path.parent / "ai_cache.json")))

    log.info("engines ready: %s playbooks, %s cluster types",
             app.state.playbook_count, app.state.cluster_type_count)
    yield
    # nothing to release: the engines hold no connections, files or threads


app = FastAPI(
    title="UC3 Monitoring Alert Triage API",
    description="Thin HTTP layer over the existing investigation and clustering engines. "
                "Two endpoints write, each to its own store: POST /alerts appends an alert "
                "to the dataset, and PUT /alerts/{alert_id}/routing records the analyst's "
                "routing decision in routing.json. Nothing writes the knowledge base.",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(router)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """
    Last resort. The traceback and any filesystem path go to the server log and
    never to the client.
    """
    log.exception("unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "internal server error"})
