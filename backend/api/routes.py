"""
Routes. Deliberately thin: validate, call the engine, shape the response.

No business logic lives here. Nothing is recomputed, reordered or reinterpreted.
Each handler is HTTP request -> Pydantic -> engine -> response model.

The engines are supplied by main.py through dependency functions, so this module
never constructs them and never reaches for global state directly.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from backend.ai import AIAnalyst, build_summary_input
from backend.ai.analyst import INSTRUCTIONS, check_output, output_schema
from backend.ai.cache import cached_or_generate, fingerprint, prompt_version
from backend.ai.cluster_summary import (
    CLUSTER_INSTRUCTIONS,
    build_cluster_input,
    check_cluster_output,
    cluster_output_schema,
    summarize_cluster,
)
from backend.api.schemas import (
    AISummaryOut,
    AlertCreate,
    AlertFormOptionsOut,
    AlertListOut,
    AlertOut,
    ClusterAISummaryOut,
    ClusterListOut,
    ClusterOut,
    ClusterRequest,
    ClusterResponse,
    ClusterRoutingIn,
    ClusterRoutingRecordOut,
    ClusterSummaryOut,
    ClusterSummaryRequest,
    CustomerOption,
    EvidenceOut,
    HealthOut,
    InvestigateRequest,
    InvestigationOut,
    InvestigationSummaryOut,
    ProgrammeOption,
    RoutingIn,
    RoutingListOut,
    RoutingOption,
    RoutingRecordOut,
    RuleOption,
    ToolCallOut,
)
from backend.routing import DESTINATIONS, OUTCOMES, RoutingStore
from backend.tools import DataStore
from backend.tools.datastore import DuplicateAlertError
from backend.clustering import ClusterEngine
from backend.investigation import InvestigationEngine

router = APIRouter()


def get_investigation_engine(request: Request) -> InvestigationEngine:
    return request.app.state.investigation_engine


def get_cluster_engine(request: Request) -> ClusterEngine:
    return request.app.state.cluster_engine


def get_datastore(request: Request) -> DataStore:
    return request.app.state.datastore


def get_ai_analyst(request: Request) -> AIAnalyst:
    return request.app.state.ai_analyst


def get_routing_store(request: Request) -> RoutingStore:
    return request.app.state.routing_store


def _require_routable(engine: InvestigationEngine, alert: dict) -> None:
    """The engine raises KeyError for a rule_id it cannot route. That is a
    client-side problem, so translate it before the engine is called rather
    than letting it surface as a 500."""
    if alert["rule_id"] not in engine.routing:
        raise HTTPException(
            status_code=400,
            detail=f"unknown rule_id '{alert['rule_id']}'; it is not routed by the "
                   f"knowledge base")


def _alert_out(a: dict) -> AlertOut:
    """Project a stored alert onto the response model, dropping `_`-prefixed
    generator metadata. Reads only; the stored dict is never modified."""
    return AlertOut(**{k: v for k, v in a.items() if not k.startswith("_")})


@router.get("/health", response_model=HealthOut, tags=["health"])
def health(request: Request) -> HealthOut:
    """Liveness plus the loaded KB identity. Runs no investigation."""
    s = request.app.state
    return HealthOut(
        status="ok",
        kb_version=s.kb_version,
        playbooks=s.playbook_count,
        cluster_types=s.cluster_type_count,
    )


@router.post("/investigate", response_model=InvestigationOut, tags=["investigation"])
def investigate(
    body: InvestigateRequest,
    engine: InvestigationEngine = Depends(get_investigation_engine),
) -> InvestigationOut:
    """
    Run the existing investigation for one already-generated alert.

    An unknown customer or programme reference is NOT an error: the engine
    returns a complete pack whose unresolvable items are MISSING. That is
    existing semantics and is preserved.
    """
    alert = body.alert.model_dump(exclude_none=False)
    _require_routable(engine, alert)
    pack = engine.investigate(alert)

    return InvestigationOut(
        alert_id=pack.alert_id,
        rule_id=pack.rule_id,
        category=pack.category,
        playbook_id=pack.playbook_id,
        cluster_ref=pack.cluster_ref,
        evidence=[
            EvidenceOut(key=e.key, status=e.status, value=e.value, tool=e.tool, field=e.field)
            for e in pack.evidence
        ],
        missing_evidence=pack.missing,
        tool_calls=[ToolCallOut(tool=c["tool"], args=c["args"]) for c in pack.tool_calls],
        investigation_focus=pack.investigation_focus,
        checklist=pack.checklist,
        escalation_guidance=pack.escalation_guidance,
        ai_boundaries=pack.ai_boundaries,
        allowed_outcomes=pack.allowed_outcomes,
    )


@router.post("/investigate/summary", response_model=InvestigationSummaryOut,
             tags=["investigation"])
def investigate_summary(
    body: InvestigateRequest,
    request: Request,
    regenerate: bool = Query(False, description="bypass the cache and make a fresh model call"),
    engine: InvestigationEngine = Depends(get_investigation_engine),
    analyst: AIAnalyst = Depends(get_ai_analyst),
) -> InvestigationSummaryOut:
    """
    AI analyst summary of one investigation.

    Runs the same deterministic investigation as POST /investigate, then asks
    the model to explain the resulting evidence pack. The model receives only
    that pack (see build_summary_input) and its answer is accepted only if
    every statement cites evidence that exists in it.

    AI failure is not an HTTP failure: a missing key, an API error or a rejected
    answer return 200 with ai_summary.status "unavailable" or "error". POST
    /investigate does not depend on this endpoint and is unaffected by it.

    Accepted summaries are cached by a fingerprint of the exact model input,
    prompt, schema and model, and re-checked against the current grounding
    checks before reuse; a repeat request makes no model call. regenerate=true
    forces a fresh call and replaces the cached summary only if it is accepted.
    """
    alert = body.alert.model_dump(exclude_none=False)
    _require_routable(engine, alert)
    pack = engine.investigate(alert)

    payload = build_summary_input(engine, pack)
    schema = output_schema(list(payload["evidence"]))
    result = cached_or_generate(
        request.app.state.ai_cache,
        kind="alert",
        key=fingerprint("alert", INSTRUCTIONS, schema, analyst.model, payload),
        version=prompt_version(INSTRUCTIONS, schema),
        regenerate=regenerate,
        # The model's own answer (sentences, findings, attention) is what is
        # re-checked; summary and missing_information are assembled from it.
        output_fields=("summary_sentences", "key_findings", "analyst_attention"),
        recheck=lambda out: check_output(out, payload, analyst.outcome_names),
        generate=lambda: analyst.summarize(payload),
    )

    cited: set[str] = set(result["summary_evidence_keys"])
    for name in ("key_findings", "missing_information", "analyst_attention"):
        for item in result[name]:
            cited |= set(item["evidence_keys"])
    basis = [
        EvidenceOut(key=e.key, status=e.status, value=e.value, tool=e.tool, field=e.field)
        for e in pack.evidence if e.key in cited
    ]

    return InvestigationSummaryOut(
        alert_id=pack.alert_id,
        rule_id=pack.rule_id,
        playbook_id=pack.playbook_id,
        ai_summary=AISummaryOut(**result, evidence_basis=[b.model_dump() for b in basis]),
    )


@router.post("/cluster", response_model=ClusterResponse, tags=["clustering"])
def cluster(
    body: ClusterRequest,
    engine: ClusterEngine = Depends(get_cluster_engine),
) -> ClusterResponse:
    """
    Run the existing cluster detection over a set of alerts.

    Thresholds may be overridden per request; the override is passed straight
    through to detect() and is never written back to the KB.
    """
    if body.overrides:
        unknown = sorted(set(body.overrides) - set(engine.clusters))
        if unknown:
            raise HTTPException(
                status_code=400,
                detail=f"unknown cluster type(s) in overrides: {unknown}; "
                       f"configured types are {sorted(engine.clusters)}")

    alerts = [a.model_dump(exclude_none=False) for a in body.alerts]

    # detect() reads created_at for its time window and raises KeyError without it.
    missing_ts = [a["alert_id"] for a in alerts if not a.get("created_at")]
    if missing_ts:
        raise HTTPException(
            status_code=400,
            detail=f"created_at is required for clustering; missing on: {missing_ts[:5]}")

    found, membership = engine.detect(alerts, overrides=body.overrides)

    return ClusterResponse(
        clusters=[
            ClusterOut(
                cluster_ref=c.cluster_ref,
                cluster_type=c.cluster_type,
                label=c.label,
                cluster_category=c.cluster_category,
                alert_ids=c.alert_ids,
                customer_refs=c.customer_refs,
                programme_refs=c.programme_refs,
                rule_ids=c.rule_ids,
                time_span_hours=c.time_span_hours,
                thresholds=c.thresholds,
            )
            for c in found
        ],
        membership=membership,
        alerts_submitted=len(alerts),
        alerts_clustered=len(membership),
    )


@router.get("/alerts", response_model=AlertListOut, tags=["alerts"])
def list_alerts(
    rule_id: str | None = Query(None, description="exact match on the upstream rule ID"),
    programme_ref: str | None = Query(None, description="exact match on the programme"),
    customer_ref: str | None = Query(None, description="exact match on the customer"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    store: DataStore = Depends(get_datastore),
) -> AlertListOut:
    """
    List alerts held in the dataset.

    Filters are exact-match on fields every alert carries. Paging exists because
    the response is a list of unbounded length; the current dataset holds 90.
    """
    rows = store.alerts
    if rule_id is not None:
        rows = [a for a in rows if a["rule_id"] == rule_id]
    if programme_ref is not None:
        rows = [a for a in rows if a["programme_ref"] == programme_ref]
    if customer_ref is not None:
        rows = [a for a in rows if a["customer_ref"] == customer_ref]

    total = len(rows)
    page = rows[offset: offset + limit]
    return AlertListOut(alerts=[_alert_out(a) for a in page],
                        total=total, limit=limit, offset=offset)


_ALERT_NUMBER = re.compile(r"ALRT-(\d+)")


def _next_alert_id(alerts: list[dict]) -> str:
    """One above the highest ALRT-<number>, at the same zero-padded width
    (at least four digits). IDs in any other format are ignored."""
    nums = [m for a in alerts if (m := _ALERT_NUMBER.fullmatch(a["alert_id"]))]
    if not nums:
        return "ALRT-0001"
    width = max(4, *(len(m.group(1)) for m in nums))
    return f"ALRT-{max(int(m.group(1)) for m in nums) + 1:0{width}d}"


# Declared before /alerts/{alert_id} so "form-options" is not read as an alert ID.
@router.get("/alerts/form-options", response_model=AlertFormOptionsOut, tags=["alerts"])
def alert_form_options(
    engine: InvestigationEngine = Depends(get_investigation_engine),
    store: DataStore = Depends(get_datastore),
) -> AlertFormOptionsOut:
    """
    The values the Add Alert form may offer: the rules the knowledge base
    routes (with each playbook's category), the customers in the dataset with
    their programme, the programmes, and a suggested next alert ID.

    Read-only. Nothing here is a second copy of the configuration: rules come
    from the same routing table investigation uses, customers and programmes
    from the dataset POST /alerts validates against.
    """
    customers = store.d.get("customers", {})
    programmes = store.d.get("programmes", {})
    return AlertFormOptionsOut(
        rules=[RuleOption(rule_id=r, playbook_id=pb, category=engine.playbooks[pb]["category"])
               for r, pb in engine.routing.items()],
        customers=[CustomerOption(customer_ref=ref, programme_ref=c.get("programme_ref"),
                                  name=c.get("business_name_placeholder"))
                   for ref, c in sorted(customers.items())],
        programmes=[ProgrammeOption(programme_ref=ref, name=p.get("programme_name_placeholder"))
                    for ref, p in sorted(programmes.items())],
        next_alert_id=_next_alert_id(store.alerts),
        alert_ids=[a["alert_id"] for a in store.alerts],
    )


@router.get("/alerts/{alert_id}", response_model=AlertOut, tags=["alerts"])
def get_alert(alert_id: str, store: DataStore = Depends(get_datastore)) -> AlertOut:
    """Fetch one alert by its stable dataset identifier."""
    for a in store.alerts:
        if a["alert_id"] == alert_id:
            return _alert_out(a)
    raise HTTPException(status_code=404, detail=f"no alert with alert_id '{alert_id}'")


@router.get("/clusters", response_model=ClusterListOut, tags=["clustering"])
def list_clusters(
    engine: ClusterEngine = Depends(get_cluster_engine),
    store: DataStore = Depends(get_datastore),
) -> ClusterListOut:
    """
    Clusters detected over the whole dataset at the configured thresholds.

    Clusters are computed, not stored: the dataset records no cluster
    membership. This runs the same detection POST /cluster runs, over every
    alert, with no overrides.
    """
    found, membership = engine.detect(store.alerts)
    return ClusterListOut(
        clusters=[
            ClusterOut(
                cluster_ref=c.cluster_ref, cluster_type=c.cluster_type, label=c.label,
                cluster_category=c.cluster_category, alert_ids=c.alert_ids,
                customer_refs=c.customer_refs, programme_refs=c.programme_refs,
                rule_ids=c.rule_ids, time_span_hours=c.time_span_hours,
                thresholds=c.thresholds,
            )
            for c in found
        ],
        membership=membership,
        alerts_considered=len(store.alerts),
        alerts_clustered=len(membership),
        note="cluster_ref is assigned per detection run and is not a durable "
             "identifier; it is stable only for identical input",
    )


@router.post("/alerts", response_model=AlertOut, status_code=201, tags=["alerts"])
def create_alert(
    body: AlertCreate,
    engine: InvestigationEngine = Depends(get_investigation_engine),
    store: DataStore = Depends(get_datastore),
) -> AlertOut:
    """
    Persist a new alert into the dataset.

    This is not POST /investigate. That one runs an investigation over an alert
    supplied in the request and stores nothing; this one adds an alert to the
    monitoring dataset so every later read, investigation and clustering run
    sees it.

    The write goes through DataStore.add_alert, which re-reads the file, checks
    for a duplicate, appends and swaps the file atomically. Because the
    InvestigationEngine holds this same DataStore instance and /clusters reads
    store.alerts at request time, both engines observe the new alert without
    anything being rebuilt.

    Before writing, the alert must be one the system can work with: the rule is
    routed by the knowledge base (so it can be investigated), the customer
    exists in the dataset, and the programme is the customer's own. The
    programme is taken from the customer record; a supplied value that differs
    is refused rather than stored. These failures are 422, in the same shape
    as FastAPI's field validation errors.

    An alert_id that is already used is 409 whatever else is wrong, checked
    first here and again inside add_alert, which remains the final authority.
    """
    duplicate = HTTPException(
        status_code=409,
        detail=f"alert_id '{body.alert_id}' already exists; "
               f"alert_id must be unique and existing alerts are never overwritten")
    if any(a["alert_id"] == body.alert_id for a in store.alerts):
        raise duplicate

    errors: list[dict] = []

    def invalid(field: str, msg: str) -> None:
        errors.append({"loc": ["body", field], "msg": msg, "type": "value_error"})

    if body.rule_id not in engine.routing:
        invalid("rule_id", f"Unknown rule '{body.rule_id}'. Choose one of the rules "
                           f"defined in the knowledge base.")
    customer = store.get_customer(body.customer_ref)
    programme = customer.get("programme_ref") if customer else None
    if not customer:
        invalid("customer_ref", f"Unknown customer '{body.customer_ref}'. Choose an "
                                f"existing customer.")
    elif programme is None and body.programme_ref is None:
        invalid("programme_ref", f"The programme of customer '{body.customer_ref}' is "
                                 f"not recorded, so it cannot be derived.")
    elif programme is not None and body.programme_ref not in (None, programme):
        invalid("programme_ref", f"Programme '{body.programme_ref}' does not match "
                                 f"customer '{body.customer_ref}', which belongs to "
                                 f"'{programme}'.")
    if errors:
        raise HTTPException(status_code=422, detail=errors)

    record = body.model_dump()
    record["programme_ref"] = programme or body.programme_ref
    try:
        stored = store.add_alert(record)
    except DuplicateAlertError:
        raise duplicate
    except OSError:
        # The atomic write failed. The original file is intact. Log server-side
        # via the catch-all handler; tell the client nothing about the path.
        raise HTTPException(status_code=500, detail="could not persist the alert")

    return _alert_out(stored)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _check_outcome(engine: InvestigationEngine, alerts: list[dict], outcome: str) -> None:
    """
    An outcome must be permitted by every affected alert's playbook, read from
    that playbook's allowed_outcomes in the knowledge base. 409 otherwise.
    """
    for alert in alerts:
        playbook_id = engine.routing.get(alert["rule_id"])
        if playbook_id is None:
            raise HTTPException(
                status_code=409,
                detail=f"outcome cannot be checked: rule_id '{alert['rule_id']}' of "
                       f"{alert['alert_id']} is not routed by the knowledge base")
        allowed = engine.playbooks[playbook_id].get("allowed_outcomes", engine.outcomes)
        if outcome not in allowed:
            raise HTTPException(
                status_code=409,
                detail=f"outcome '{outcome}' is not permitted by playbook '{playbook_id}'; "
                       f"its allowed outcomes are {list(allowed)}")


def _current_cluster(cluster_engine: ClusterEngine, store: DataStore, alert_ids: list[str]):
    """
    The cluster detected right now whose members are exactly alert_ids. The
    client identifies a cluster by its members, never by cluster_ref, and can
    only act on a cluster the engine currently detects.
    """
    found, _ = cluster_engine.detect(store.alerts)
    wanted = set(alert_ids)
    match = next((c for c in found if set(c.alert_ids) == wanted), None)
    if match is None:
        raise HTTPException(
            status_code=409,
            detail="these alerts are not a currently detected cluster; clusters are recomputed "
                   "on every request, so the membership may have changed")
    return match


@router.get("/routing", response_model=RoutingListOut, tags=["routing"])
def list_routing(routing: RoutingStore = Depends(get_routing_store)) -> RoutingListOut:
    """
    The routing options and the decision in force for every routed alert, from
    the alert itself or from a routed cluster (whichever was made later). Also
    every cluster decision, each for the exact member list it was made for.
    Read-only. An alert absent from `routing` has not been routed.
    """
    return RoutingListOut(
        destinations=[RoutingOption(id=k, label=v[0], description=v[1]) for k, v in DESTINATIONS.items()],
        outcomes=[RoutingOption(id=k, label=v[0], description=v[1]) for k, v in OUTCOMES.items()],
        routing={k: RoutingRecordOut(**v) for k, v in routing.effective().items()},
        clusters=[ClusterRoutingRecordOut(**c) for c in routing.clusters().values()],
    )


@router.put("/alerts/{alert_id}/routing", response_model=RoutingRecordOut, tags=["routing"])
def set_routing(
    alert_id: str,
    body: RoutingIn,
    store: DataStore = Depends(get_datastore),
    engine: InvestigationEngine = Depends(get_investigation_engine),
    routing: RoutingStore = Depends(get_routing_store),
) -> RoutingRecordOut:
    """
    Record the analyst's routing decision for one alert. Writes data/routing.json
    only; the alert dataset is never modified. A later call replaces the earlier
    decision for this alert, and a decision made here takes precedence over an
    earlier cluster routing for this alert only.

    An outcome must be permitted by the alert's playbook. Destinations are always
    available. This does not re-run the investigation or call the AI.
    """
    alert = next((a for a in store.alerts if a["alert_id"] == alert_id), None)
    if alert is None:
        raise HTTPException(status_code=404, detail=f"no alert with alert_id '{alert_id}'")
    if body.outcome is not None:
        _check_outcome(engine, [alert], body.outcome)

    record = {"alert_id": alert_id, "destination": body.destination, "outcome": body.outcome,
              "routed_at": _now()}
    try:
        stored = routing.set_alert(record)
    except OSError:
        raise HTTPException(status_code=500, detail="could not persist the routing decision")
    return RoutingRecordOut(**stored)


@router.put("/clusters/routing", response_model=ClusterRoutingRecordOut, tags=["routing"])
def set_cluster_routing(
    body: ClusterRoutingIn,
    store: DataStore = Depends(get_datastore),
    engine: InvestigationEngine = Depends(get_investigation_engine),
    cluster_engine: ClusterEngine = Depends(get_cluster_engine),
    routing: RoutingStore = Depends(get_routing_store),
) -> ClusterRoutingRecordOut:
    """
    Route a currently detected cluster, identified by its exact member list.

    The decision becomes the routing in force for every current member, taking
    precedence over earlier individual decisions, which are kept. A member routed
    individually afterwards uses its own decision. The decision is stored for this
    exact membership: if the cluster later gains or loses members it is a new,
    unrouted cluster, and the old decision is never applied to the new membership.

    An outcome must be permitted by every member's playbook. Writes
    data/routing.json only; nothing is re-run and no AI is called.
    """
    cluster = _current_cluster(cluster_engine, store, body.alert_ids)
    members = [a for a in store.alerts if a["alert_id"] in set(cluster.alert_ids)]
    if body.outcome is not None:
        _check_outcome(engine, members, body.outcome)

    record = {"cluster_type": cluster.cluster_type, "label": cluster.label,
              "alert_ids": list(cluster.alert_ids), "destination": body.destination,
              "outcome": body.outcome, "routed_at": _now()}
    try:
        stored = routing.set_cluster(record)
    except OSError:
        raise HTTPException(status_code=500, detail="could not persist the routing decision")
    return ClusterRoutingRecordOut(**stored)


@router.post("/clusters/summary", response_model=ClusterSummaryOut, tags=["clustering"])
def cluster_summary(
    body: ClusterSummaryRequest,
    request: Request,
    regenerate: bool = Query(False, description="bypass the cache and make a fresh model call"),
    store: DataStore = Depends(get_datastore),
    cluster_engine: ClusterEngine = Depends(get_cluster_engine),
    analyst: AIAnalyst = Depends(get_ai_analyst),
) -> ClusterSummaryOut:
    """
    AI explanation of why the deterministic engine grouped a currently detected
    cluster, identified by its exact member list.

    The model receives only the deterministic cluster result, the cluster type's
    configured match rules, the member alert records and the KB cluster purpose
    and boundaries. It does not decide membership or recommend routing. AI failure
    is a 200 with ai_summary.status "unavailable" or "error".
    """
    cluster = _current_cluster(cluster_engine, store, body.alert_ids)
    members = [a for a in store.alerts if a["alert_id"] in set(cluster.alert_ids)]
    members.sort(key=lambda a: cluster.alert_ids.index(a["alert_id"]))
    payload = build_cluster_input(
        {k: getattr(cluster, k) for k in ("cluster_type", "label", "cluster_category", "alert_ids",
                                          "customer_refs", "programme_refs", "rule_ids",
                                          "time_span_hours", "thresholds")},
        cluster_engine.clusters[cluster.cluster_type].get("match_on", {}),
        members,
        request.app.state.cluster_guidance.get(cluster.cluster_type, {}),
    )
    labels = [v[0] for v in DESTINATIONS.values()]
    schema = cluster_output_schema()
    result = cached_or_generate(
        request.app.state.ai_cache,
        kind="cluster",
        key=fingerprint("cluster", CLUSTER_INSTRUCTIONS, schema, analyst.model, payload),
        version=prompt_version(CLUSTER_INSTRUCTIONS, schema),
        regenerate=regenerate,
        output_fields=("summary", "summary_fields", "criteria_met", "characteristics"),
        recheck=lambda out: check_cluster_output(out, payload, labels, analyst.outcome_names),
        generate=lambda: summarize_cluster(analyst, payload, labels),
    )
    return ClusterSummaryOut(cluster_type=cluster.cluster_type, label=cluster.label,
                             alert_ids=list(cluster.alert_ids), ai_summary=ClusterAISummaryOut(**result))
