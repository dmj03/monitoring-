"""
API request and response models.

Every field here is derived from the actual engine interfaces, not invented:

  AlertIn        - the keys investigate() and detect() read from an alert dict.
                   alert_id, rule_id, customer_ref and programme_ref are
                   required because the engines raise KeyError without them.
                   created_at is required for clustering's time window.
                   shared_match_key and cluster_ref are optional, matching the
                   dataset, where they are frequently null.

  EvidenceOut    - one EvidenceItem: key, status (FOUND|MISSING), value, and
                   the tool/field it came from.

  InvestigationOut - the EvidencePack fields, unchanged in meaning. Nothing is
                   added, renamed or recomputed.

  ClusterOut     - the Cluster dataclass fields, unchanged.

The models exist so responses are JSON-serialisable and documented. They do not
reshape engine semantics: MISSING stays MISSING with no value, allowed_outcomes
stays the playbook's subset, and cluster thresholds stay as the engine reported
them.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from backend.routing import DESTINATIONS, OUTCOMES


class AlertIn(BaseModel):
    """An already-generated monitoring alert, as the engines expect it."""

    model_config = ConfigDict(extra="allow")  # the engines tolerate extra keys

    alert_id: str = Field(min_length=1)
    rule_id: str = Field(min_length=1)
    customer_ref: str = Field(min_length=1)
    programme_ref: str = Field(min_length=1)
    created_at: str | None = None
    shared_match_key: str | None = None
    cluster_ref: str | None = None


class InvestigateRequest(BaseModel):
    alert: AlertIn


class ClusterRequest(BaseModel):
    alerts: list[AlertIn] = Field(min_length=1)
    overrides: dict[str, dict[str, int]] | None = Field(
        default=None,
        description="Per-cluster threshold overrides, e.g. "
                    '{"repeated_velocity": {"min_alerts": 7}}. Applied in memory '
                    "for this request only; the KB configuration is not modified.")


class ToolCallOut(BaseModel):
    tool: str
    args: dict[str, Any]


class EvidenceOut(BaseModel):
    key: str
    status: str
    value: Any = None
    tool: str
    field: str


class InvestigationOut(BaseModel):
    alert_id: str
    rule_id: str
    category: str
    playbook_id: str
    cluster_ref: str | None = None
    evidence: list[EvidenceOut]
    missing_evidence: list[str]
    tool_calls: list[ToolCallOut]
    investigation_focus: str
    checklist: list[str]
    escalation_guidance: str
    ai_boundaries: str
    allowed_outcomes: list[str]


class AIStatementOut(BaseModel):
    text: str
    evidence_keys: list[str] = Field(
        description="evidence keys from the investigation this statement relies on")


class EvidenceBasisOut(BaseModel):
    """One cited evidence item, copied from the investigation, not from the model."""
    key: str
    status: str
    value: Any = None
    tool: str
    field: str


class AISummaryOut(BaseModel):
    """
    The AI analyst summary. Deliberately has no outcome, decision, escalation or
    confidence field: the model explains evidence and the analyst decides.
    """
    status: Literal["generated", "unavailable", "error"]
    label: Literal["AI-generated"] = "AI-generated"
    generated_by: Literal["llm"] | None = Field(
        None, description='"llm" when a model produced this summary; null otherwise')
    model: str | None = Field(None, description="value of LLM_MODEL (or OPENAI_MODEL)")
    summary: str | None = Field(None, description="the narrative: summary_sentences joined")
    summary_evidence_keys: list[str] = []
    summary_sentences: list[AIStatementOut] = Field(
        [], description="the narrative, one sentence per item, each with its citations")
    key_findings: list[AIStatementOut] = []
    missing_information: list[AIStatementOut] = []
    analyst_attention: list[AIStatementOut] = []
    evidence_basis: list[EvidenceBasisOut] = Field(
        [], description="the investigation's own record for every cited key")
    reason: str | None = Field(None, description="why no summary was produced")
    cached: bool = Field(False, description="true when this accepted summary was reused from the cache")
    generated_at: str | None = Field(None, description="when the model produced this summary, ISO-8601 UTC")


class InvestigationSummaryOut(BaseModel):
    alert_id: str
    rule_id: str
    playbook_id: str
    ai_summary: AISummaryOut


class ClusterOut(BaseModel):
    cluster_ref: str
    cluster_type: str
    label: str
    cluster_category: str
    alert_ids: list[str]
    customer_refs: list[str]
    programme_refs: list[str]
    rule_ids: list[str]
    time_span_hours: float
    thresholds: dict[str, Any]


class ClusterResponse(BaseModel):
    clusters: list[ClusterOut]
    membership: dict[str, str] = Field(
        description="alert_id -> cluster_ref, for alerts that joined a cluster")
    alerts_submitted: int
    alerts_clustered: int


class HealthOut(BaseModel):
    status: str
    kb_version: str
    playbooks: int
    cluster_types: int
    alert_creation: bool = Field(description="whether POST /alerts accepts new alerts (UC3_ALERT_CREATION)")


class ErrorOut(BaseModel):
    detail: str

# --------------------------------------------------------------------------
# Read endpoints
# --------------------------------------------------------------------------


class AlertOut(BaseModel):
    """
    One alert as stored in the dataset.

    Fields are exactly the alert record's business keys. The dataset also
    carries `_tag` and `_category_intent`, which are generator/test metadata
    describing which scenario produced the alert; those are deliberately not
    exposed, because they are not part of the monitoring alert an upstream
    system would supply.
    """

    alert_id: str
    rule_id: str
    customer_ref: str
    programme_ref: str
    created_at: str | None = None
    shared_match_key: str | None = None
    cluster_ref: str | None = None


class AlertListOut(BaseModel):
    alerts: list[AlertOut]
    total: int = Field(description="matching alerts before paging")
    limit: int
    offset: int


class ClusterListOut(BaseModel):
    clusters: list[ClusterOut]
    membership: dict[str, str]
    alerts_considered: int
    alerts_clustered: int
    note: str = Field(
        description="cluster_ref is assigned per detection run and is not a durable "
                    "identifier; it is stable only for identical input")


class AlertCreate(BaseModel):
    """
    Body for POST /alerts.

    Deliberately stricter than AlertIn, which allows extra keys because the
    engines tolerate them. Creation forbids extras so generator-only fields
    such as _tag and _category_intent cannot be injected into the dataset
    through the API, and so a typo is rejected rather than silently stored.

    The field set is exactly the business keys an alert record carries.

    Checks that need the knowledge base or the dataset (rule_id is routed,
    customer_ref exists, programme_ref matches the customer) are made by the
    route, which holds both. programme_ref is optional: the route derives it
    from the customer record, and a supplied value must agree with it.

    cluster_ref is accepted only as null. Cluster membership is computed by the
    cluster engine; a stored value would be copied into the investigation and
    the AI input as if it were a cluster the alert belonged to.
    """

    model_config = ConfigDict(extra="forbid")

    alert_id: str = Field(min_length=1)
    rule_id: str = Field(min_length=1)
    customer_ref: str = Field(min_length=1)
    programme_ref: str | None = Field(
        default=None, min_length=1,
        description="optional; derived from the customer record, and must match it if supplied")
    created_at: str = Field(min_length=1,
                            description="ISO-8601 with a UTC offset, e.g. "
                                        "2026-10-03T14:30:00Z; required because clustering "
                                        "needs it")
    shared_match_key: str | None = None
    cluster_ref: None = Field(
        default=None,
        description="must be null; cluster membership is calculated by the cluster engine")

    @field_validator("cluster_ref", mode="before")
    @classmethod
    def cluster_ref_is_not_assigned(cls, v: Any) -> None:
        if v is not None:
            raise ValueError(
                "cluster_ref cannot be set when creating an alert; cluster membership "
                "is calculated by the cluster engine")
        return None

    @field_validator("created_at")
    @classmethod
    def created_at_is_clusterable(cls, v: str) -> str:
        """
        Reject a timestamp the cluster engine could not use, before it is
        persisted. Once stored, a bad value breaks every later GET /clusters,
        because detection parses created_at for every alert.

        The parse mirrors ClusterEngine._ts exactly. An offset is also required:
        the dataset's timestamps are all UTC-aware, and a naive one parses but
        cannot be compared with them. The value is stored as submitted, not
        normalised.
        """
        try:
            ts = datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError:
            raise ValueError(
                "created_at must be an ISO-8601 timestamp, e.g. 2026-10-03T14:30:00Z"
            ) from None
        if ts.tzinfo is None:
            raise ValueError(
                "created_at must include a UTC offset, e.g. 2026-10-03T14:30:00Z "
                "or 2026-10-03T15:30:00+01:00")
        return v


class RuleOption(BaseModel):
    rule_id: str
    playbook_id: str
    category: str


class CustomerOption(BaseModel):
    customer_ref: str
    programme_ref: str | None
    name: str | None = Field(description="the customer record's business name, as stored")


class ProgrammeOption(BaseModel):
    programme_ref: str
    name: str | None = Field(description="the programme record's name, as stored")


class AlertFormOptionsOut(BaseModel):
    """
    What the Add Alert form may offer. Read-only; every value comes from the
    knowledge base routing table, the playbooks, or the dataset.
    """

    rules: list[RuleOption] = Field(description="rules routed by the knowledge base, in config order")
    customers: list[CustomerOption]
    programmes: list[ProgrammeOption]
    next_alert_id: str = Field(description="one above the highest ALRT-<number> currently stored")
    alert_ids: list[str] = Field(description="alert IDs already in use")


# --------------------------------------------------------------------------
# Routing: the analyst's decision, persisted in data/routing.json
# --------------------------------------------------------------------------


class RoutingIn(BaseModel):
    """
    Body for PUT /alerts/{alert_id}/routing: exactly one of a destination
    (where the alert is sent) or an outcome (closed without routing).
    """

    model_config = ConfigDict(extra="forbid")

    destination: str | None = None
    outcome: str | None = None

    @field_validator("destination")
    @classmethod
    def known_destination(cls, v: str | None) -> str | None:
        if v is not None and v not in DESTINATIONS:
            raise ValueError(f"unknown destination; expected one of {sorted(DESTINATIONS)}")
        return v

    @field_validator("outcome")
    @classmethod
    def known_outcome(cls, v: str | None) -> str | None:
        if v is not None and v not in OUTCOMES:
            raise ValueError(f"unknown outcome; expected one of {sorted(OUTCOMES)}")
        return v

    @model_validator(mode="after")
    def exactly_one(self) -> "RoutingIn":
        if (self.destination is None) == (self.outcome is None):
            raise ValueError("give exactly one of destination or outcome")
        return self


class RoutingRecordOut(BaseModel):
    alert_id: str
    destination: str | None = None
    outcome: str | None = None
    routed_at: str = Field(description="server time of the decision, ISO-8601 UTC")
    source: Literal["alert", "cluster"] = Field(
        "alert", description='"cluster" when the decision in force came from routing a cluster')
    cluster_type: str | None = None
    cluster_label: str | None = None


class RoutingOption(BaseModel):
    id: str
    label: str
    description: str


class ClusterRoutingIn(RoutingIn):
    """
    Body for PUT /clusters/routing. The cluster is identified by its exact
    current member list, because cluster_ref is only a per-run reference.
    """

    alert_ids: list[str] = Field(min_length=1)


class ClusterRoutingRecordOut(BaseModel):
    key: str = Field(description="cluster_type plus the sorted member alert_ids")
    cluster_type: str
    label: str
    alert_ids: list[str]
    destination: str | None = None
    outcome: str | None = None
    routed_at: str


class RoutingListOut(BaseModel):
    destinations: list[RoutingOption]
    outcomes: list[RoutingOption]
    routing: dict[str, RoutingRecordOut] = Field(
        description="alert_id -> the decision in force for it, from the alert itself or from "
                    "a routed cluster; alerts absent here are not routed")
    clusters: list[ClusterRoutingRecordOut] = Field(
        [], description="decisions made on clusters, each for an exact member list")


class ClusterSummaryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    alert_ids: list[str] = Field(min_length=1, description="the cluster's exact current members")


class ClusterAIStatementOut(BaseModel):
    text: str
    fields: list[str] = Field(description="cluster-data fields this statement relies on")


class ClusterAISummaryOut(BaseModel):
    """AI explanation of a deterministic cluster. No routing, outcome, decision or confidence field."""
    status: Literal["generated", "unavailable", "error"]
    label: Literal["AI-generated"] = "AI-generated"
    generated_by: Literal["llm"] | None = None
    model: str | None = None
    summary: str | None = None
    summary_fields: list[str] = []
    criteria_met: list[ClusterAIStatementOut] = []
    characteristics: list[ClusterAIStatementOut] = []
    reason: str | None = None
    cached: bool = Field(False, description="true when this accepted summary was reused from the cache")
    generated_at: str | None = Field(None, description="when the model produced this summary, ISO-8601 UTC")


class ClusterSummaryOut(BaseModel):
    cluster_type: str
    label: str
    alert_ids: list[str]
    ai_summary: ClusterAISummaryOut
