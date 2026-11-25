import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import Alerts from "../pages/Alerts";
import AlertDetail from "../pages/AlertDetail";
import Clusters from "../pages/Clusters";
import type { Alert } from "../api/types";
import { fmtDetected, istToUtcIso } from "../lib/format";

// Every test mocks fetch. No test touches a real server or the dataset.
const alert = (id: string, over: Partial<Alert> = {}): Alert => ({
  alert_id: id,
  rule_id: "TM-VEL-014",
  customer_ref: "CUST-0101",
  programme_ref: "P001",
  created_at: "2026-09-22T06:00:00Z",
  shared_match_key: null,
  cluster_ref: null,
  ...over,
});

const INVESTIGATION = {
  alert_id: "ALRT-0001",
  rule_id: "TM-VEL-014",
  category: "unusual_transaction_velocity",
  playbook_id: "unusual_transaction_velocity",
  cluster_ref: null,
  evidence: [
    { key: "kyb_status", status: "FOUND", value: "complete", tool: "get_customer", field: "kyb_status" },
    { key: "source_of_funds", status: "MISSING", value: null, tool: "get_customer", field: "source_of_funds" },
  ],
  missing_evidence: ["source_of_funds"],
  tool_calls: [{ tool: "get_customer", args: { customer_ref: "CUST-0101" } }],
  investigation_focus: "Establish whether the activity is explainable.",
  checklist: ["Compare against both baselines."],
  escalation_guidance: "Suggest escalation where evidence is incomplete.",
  ai_boundaries: "Do not state that the activity is suspicious.",
  allowed_outcomes: ["no_action_required", "escalate_to_mlro"],
};

// Shape of POST /investigate/summary. Test fixture only; every value is taken
// from INVESTIGATION above.
const AI_SUMMARY = {
  alert_id: "ALRT-0001",
  rule_id: "TM-VEL-014",
  playbook_id: "unusual_transaction_velocity",
  ai_summary: {
    status: "generated",
    label: "AI-generated",
    generated_by: "llm",
    model: "model-from-env",
    summary:
      "An alert for unusual transaction velocity has been raised. The KYB status is recorded as complete. " +
      "Source of funds is not available in the collected evidence.",
    summary_evidence_keys: ["kyb_status", "source_of_funds"],
    summary_sentences: [
      { text: "An alert for unusual transaction velocity has been raised.", evidence_keys: [] },
      { text: "The KYB status is recorded as complete.", evidence_keys: ["kyb_status"] },
      { text: "Source of funds is not available in the collected evidence.", evidence_keys: ["source_of_funds"] },
    ],
    key_findings: [
      { text: "KYB status: complete", evidence_keys: ["kyb_status"] },
      { text: "Source of funds: not available", evidence_keys: ["source_of_funds"] },
    ],
    missing_information: [
      { text: "Source of funds is not available in the collected evidence.", evidence_keys: ["source_of_funds"] },
    ],
    analyst_attention: [
      { text: "Source of funds was not available in the collected evidence.", evidence_keys: ["source_of_funds"] },
    ],
    evidence_basis: INVESTIGATION.evidence,
    reason: null,
  },
};

const CLUSTERS = {
  clusters: [
    {
      cluster_ref: "C001",
      cluster_type: "repeated_velocity",
      label: "Repeated Velocity Pattern",
      cluster_category: "programme_level_pattern",
      alert_ids: ["ALRT-0001", "ALRT-0002"],
      customer_refs: ["CUST-0101", "CUST-0102"],
      programme_refs: ["P001"],
      rule_ids: ["TM-VEL-014"],
      time_span_hours: 34,
      thresholds: { min_alerts: 5 },
    },
  ],
  membership: { "ALRT-0001": "C001" },
  alerts_considered: 90,
  alerts_clustered: 20,
  note: "cluster_ref is assigned per detection run and is not a durable identifier",
};

let calls: { url: string; init?: RequestInit }[] = [];

function mockFetch(handler: (url: string, init?: RequestInit) => [number, unknown]) {
  calls = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, init });
      const [status, body] = handler(url, init);
      return {
        ok: status >= 200 && status < 300,
        status,
        json: async () => body,
      } as Response;
    }),
  );
}

const listBody = (alerts: Alert[], total = alerts.length, limit = 25, offset = 0) => ({
  alerts,
  total,
  limit,
  offset,
});

beforeEach(() => {
  vi.stubGlobal("location", { origin: "http://localhost" } as Location);
});
afterEach(() => vi.unstubAllGlobals());

describe("alert queue", () => {
  it("renders the alert list from the API", async () => {
    mockFetch(() => [200, listBody([alert("ALRT-0001"), alert("ALRT-0002")], 2)]);
    render(
      <MemoryRouter>
        <Alerts />
      </MemoryRouter>,
    );
    expect(await screen.findByText("ALRT-0001")).toBeTruthy();
    expect(screen.getByText("ALRT-0002")).toBeTruthy();
  });

  it("shows an empty state when the API returns no alerts", async () => {
    mockFetch(() => [200, listBody([], 0)]);
    render(
      <MemoryRouter>
        <Alerts />
      </MemoryRouter>,
    );
    expect(await screen.findByText("No alerts found")).toBeTruthy();
  });

  it("sends filters as API query parameters", async () => {
    mockFetch(() => [200, listBody([alert("ALRT-0001")], 1)]);
    render(
      <MemoryRouter>
        <Alerts />
      </MemoryRouter>,
    );
    await screen.findByText("ALRT-0001");
    await userEvent.selectOptions(screen.getByLabelText("Rule ID"), "TM-VEL-014");
    await waitFor(() => {
      expect(calls.some((c) => c.url.includes("rule_id=TM-VEL-014"))).toBe(true);
    });
  });

  // 60 alerts: ALRT-1000 is the oldest, ALRT-1059 the newest. Returned oldest
  // first, as the API does. The list is fetched in full and paged in the UI.
  const many = (n = 60) =>
    Array.from({ length: n }, (_, i) =>
      alert(`ALRT-${1000 + i}`, { created_at: new Date(Date.UTC(2026, 8, 1, 0, i)).toISOString() }),
    );
  const ids = () => screen.getAllByRole("row").slice(1).map((r) => within(r).getAllByRole("cell")[0].textContent);

  it("shows newest first, with page controls above and below the table", async () => {
    mockFetch(() => [200, listBody(many(), 60, 500, 0)]);
    render(
      <MemoryRouter>
        <Alerts />
      </MemoryRouter>,
    );
    await screen.findByText("ALRT-1059");
    expect(ids()[0]).toBe("ALRT-1059");
    expect(ids()[24]).toBe("ALRT-1035");
    const top = within(screen.getByRole("navigation", { name: "Pagination (top)" }));
    const bottom = within(screen.getByRole("navigation", { name: "Pagination (bottom)" }));
    for (const p of [top, bottom]) {
      expect(p.getByText(/Showing 1–25 of 60/)).toBeTruthy();
      expect(p.getByText(/Page 1 of 3/)).toBeTruthy();
      expect(p.getByRole("button", { name: "Page 3" })).toBeTruthy();
    }
    expect(top.getByRole("button", { name: "Page 1" }).getAttribute("aria-current")).toBe("page");
    expect((top.getByRole("button", { name: "Previous page" }) as HTMLButtonElement).disabled).toBe(true);

    await userEvent.click(top.getByRole("button", { name: "Next page" }));
    expect(top.getByText(/Showing 26–50 of 60/)).toBeTruthy();
    expect(ids()[0]).toBe("ALRT-1034");
    await userEvent.click(bottom.getByRole("button", { name: "Page 3" }));
    expect(top.getByText(/Showing 51–60 of 60/)).toBeTruthy();
    expect(top.getByText(/Page 3 of 3/)).toBeTruthy();
    expect(ids()[ids().length - 1]).toBe("ALRT-1000");
    expect((bottom.getByRole("button", { name: "Next page" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("fetches every alert, not just the first API page", async () => {
    const rows = many(30);
    mockFetch((url) => {
      if (!url.includes("/alerts")) return [200, listBody([], 0)];
      const offset = Number(new URL(url, "http://x").searchParams.get("offset") ?? 0);
      // pretend the API holds 530 alerts, 500 per request
      return [200, listBody(offset === 0 ? rows.slice(0, 20) : rows.slice(20), 530, 500, offset)];
    });
    render(
      <MemoryRouter>
        <Alerts />
      </MemoryRouter>,
    );
    await screen.findByText("ALRT-1029");
    expect(calls.some((c) => c.url.includes("offset=500"))).toBe(true);
  });

  it("paginates only the alerts that match the destination filter", async () => {
    const rows = many(60);
    const routed: Record<string, unknown> = {};
    // 30 alerts routed to Programme Operations (the even ones), 3 closed with No Further Action
    rows.forEach((a, i) => {
      if (i % 2 === 0)
        routed[a.alert_id] = { alert_id: a.alert_id, destination: "programme_operations", outcome: null,
          routed_at: "2026-10-05T10:00:00Z", source: i < 12 ? "cluster" : "alert", cluster_label: "Repeated Velocity Pattern" };
    });
    ["ALRT-1001", "ALRT-1003", "ALRT-1005"].forEach((id) => {
      routed[id] = { alert_id: id, destination: null, outcome: "no_action_required", routed_at: "2026-10-05T10:00:00Z" };
    });
    mockFetch((url) => {
      if (url.endsWith("/routing"))
        return [200, {
          destinations: [
            { id: "mlro", label: "MLRO / Nominated Officer", description: "x" },
            { id: "programme_operations", label: "Programme Operations", description: "x" },
          ],
          outcomes: [{ id: "no_action_required", label: "No Further Action", description: "x" }],
          routing: routed, clusters: [],
        }];
      return [200, listBody(rows, 60, 500, 0)];
    });
    render(
      <MemoryRouter>
        <Alerts />
      </MemoryRouter>,
    );
    await screen.findByText("ALRT-1059");
    const destination = screen.getByLabelText("Destination") as HTMLSelectElement;
    await waitFor(() => expect(destination.disabled).toBe(false));
    await userEvent.selectOptions(destination, "destination:programme_operations");
    const top = within(screen.getByRole("navigation", { name: "Pagination (top)" }));
    expect(top.getByText(/Showing 1–25 of 30/)).toBeTruthy();
    expect(top.getByText(/Page 1 of 2/)).toBeTruthy();
    expect(ids()[0]).toBe("ALRT-1058"); // newest matching
    await userEvent.click(top.getByRole("button", { name: "Page 2" }));
    expect(top.getByText(/Showing 26–30 of 30/)).toBeTruthy();
    for (const r of screen.getAllByRole("row").slice(1)) expect(within(r).getByText("Programme Operations")).toBeTruthy();
    expect(screen.getAllByText("via cluster").length).toBe(5); // ALRT-1000..1010 even, routed via the cluster

    // changing the filter goes back to page 1
    await userEvent.selectOptions(destination, "outcome:no_action_required");
    expect(top.getByText(/Showing 1–3 of 3/)).toBeTruthy();
    expect(ids()).toEqual(["ALRT-1005", "ALRT-1003", "ALRT-1001"]);
    await userEvent.selectOptions(destination, "none");
    expect(top.getByText(/of 27/)).toBeTruthy(); // 60 - 30 routed - 3 closed
  });

  it("search covers every page, not just the visible one", async () => {
    mockFetch(() => [200, listBody(many(), 60, 500, 0)]);
    render(
      <MemoryRouter>
        <Alerts />
      </MemoryRouter>,
    );
    await screen.findByText("ALRT-1059");
    await userEvent.type(screen.getByLabelText("Search alerts"), "ALRT-1002");
    expect(await screen.findByText("ALRT-1002")).toBeTruthy(); // on the last page before searching
    expect(ids()).toEqual(["ALRT-1002"]);
  });

  it("surfaces a network failure with actionable text", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new TypeError("failed to fetch");
      }),
    );
    render(
      <MemoryRouter>
        <Alerts />
      </MemoryRouter>,
    );
    expect(await screen.findByText(/Unable to connect to the monitoring API/)).toBeTruthy();
  });
});

// GET /alerts/form-options. Test fixture only.
const FORM_OPTIONS = {
  rules: [
    { rule_id: "TM-VEL-014", playbook_id: "unusual_transaction_velocity", category: "unusual_transaction_velocity" },
    {
      rule_id: "SAN-PEP-061",
      playbook_id: "sanctions_pep_screening_review_required",
      category: "sanctions_pep_screening_review_required",
    },
  ],
  customers: [
    { customer_ref: "CUST-0101", programme_ref: "P001", name: "Synthetic Business 0101 OU" },
    { customer_ref: "CUST-0204", programme_ref: "P002", name: "Synthetic Business 0204 OU" },
  ],
  programmes: [
    { programme_ref: "P001", name: "Programme Alpha (media buying, white label)" },
    { programme_ref: "P002", name: "Programme Beta (marketplace payouts)" },
  ],
  next_alert_id: "ALRT-0091",
  alert_ids: ["ALRT-0001", "ALRT-0090"],
};

describe("add alert", () => {
  // Alerts page with the form open. `post` answers POST /alerts.
  async function openForm(post: () => [number, unknown] = () => [201, alert("ALRT-0091")]) {
    let created = false;
    mockFetch((url, init) => {
      if (url.includes("/alerts/form-options")) return [200, FORM_OPTIONS];
      if (init?.method === "POST" && url.includes("/alerts")) {
        const r = post();
        created = r[0] === 201;
        return r;
      }
      return [200, listBody(created ? [alert("ALRT-0091")] : [], created ? 1 : 0)];
    });
    render(
      <MemoryRouter>
        <Alerts />
      </MemoryRouter>,
    );
    await screen.findByText("No alerts found");
    await userEvent.click(screen.getByRole("button", { name: /Add alert/ }));
    const modal = within(screen.getByRole("dialog"));
    await waitFor(() => expect((modal.getByLabelText("Alert ID") as HTMLInputElement).value).toBe("ALRT-0091"));
    return modal;
  }

  async function pick(modal: ReturnType<typeof within>, label: string, option: RegExp, search?: string) {
    await userEvent.click(modal.getByRole("button", { name: label }));
    if (search) await userEvent.type(modal.getByLabelText(`Search ${label.toLowerCase()}`), search);
    await userEvent.click(modal.getByRole("option", { name: option }));
  }

  function detected(modal: ReturnType<typeof within>, date: string, time: string) {
    fireEvent.change(modal.getByLabelText("Detected date (IST)"), { target: { value: date } });
    fireEvent.change(modal.getByLabelText("Detected time (IST)"), { target: { value: time } });
  }

  const posts = () => calls.filter((c) => c.init?.method === "POST" && /\/alerts$/.test(c.url));

  it("suggests the next alert ID and offers only the rules and customers from form-options", async () => {
    const modal = await openForm();
    expect(modal.getByText("Automatically generated · editable")).toBeTruthy();

    await userEvent.click(modal.getByRole("button", { name: "Rule" }));
    expect(modal.getAllByRole("option").map((o) => o.textContent)).toEqual([
      "TM-VEL-014 · Unusual Transaction Velocity",
      "SAN-PEP-061 · Sanctions PEP Screening Review Required",
    ]);
    await userEvent.keyboard("{Escape}");
    await userEvent.click(modal.getByRole("button", { name: "Customer" }));
    expect(modal.getAllByRole("option").length).toBe(2);
    await userEvent.keyboard("{Escape}");

    // no free-text rule, customer or programme, and no internal fields
    for (const name of ["Rule ID", "Programme", "Shared match key", "Cluster reference", "Created at"])
      expect(modal.queryByRole("textbox", { name })).toBeNull();
    expect(modal.getByTestId("programme").textContent).toBe("Choose a customer first");
    expect(modal.getByText("Read-only · derived from customer")).toBeTruthy();
    expect(modal.getByText("IST (UTC+05:30)")).toBeTruthy();
  });

  it("creates the alert from the chosen values, converting Detected At from IST to UTC", async () => {
    const modal = await openForm();
    await pick(modal, "Rule", /TM-VEL-014/);
    await pick(modal, "Customer", /CUST-0101/);
    expect(modal.getByTestId("programme").textContent).toBe("P001");
    detected(modal, "2026-10-03", "14:30");
    expect(modal.getByTestId("utc-preview").textContent).toBe("Defaults to now · editable · stored as 2026-10-03T09:00:00Z (UTC)");

    await userEvent.click(modal.getByRole("button", { name: /Create alert/ }));
    await waitFor(() => expect(posts().length).toBe(1));
    const payload = JSON.parse(String(posts()[0].init!.body));
    // programme is derived by the server; shared_match_key and cluster_ref are never sent
    expect(payload).toEqual({
      alert_id: "ALRT-0091",
      rule_id: "TM-VEL-014",
      customer_ref: "CUST-0101",
      created_at: "2026-10-03T09:00:00Z",
    });
    expect(await screen.findByText("ALRT-0091")).toBeTruthy();
    expect(await screen.findByText(/created successfully/)).toBeTruthy();
  });

  it("customer search narrows the list, and the programme follows the customer", async () => {
    const modal = await openForm();
    await userEvent.click(modal.getByRole("button", { name: "Customer" }));
    await userEvent.type(modal.getByLabelText("Search customer"), "0204");
    expect(modal.getAllByRole("option").length).toBe(1);
    await userEvent.type(modal.getByLabelText("Search customer"), "x");
    expect(modal.getByText("No matches")).toBeTruthy();
    await userEvent.keyboard("{Escape}");
    await pick(modal, "Customer", /CUST-0204/, "0204");
    expect(modal.getByTestId("programme").textContent).toBe("P002");
  });

  it("keeps an ID the analyst edits and sends it", async () => {
    const modal = await openForm();
    const id = modal.getByLabelText("Alert ID") as HTMLInputElement;
    await userEvent.clear(id);
    await userEvent.type(id, "ALRT-5000");
    await pick(modal, "Rule", /SAN-PEP-061/);
    await pick(modal, "Customer", /CUST-0204/);
    detected(modal, "2026-10-03", "03:00");
    await userEvent.click(modal.getByRole("button", { name: /Create alert/ }));
    await waitFor(() => expect(posts().length).toBe(1));
    const payload = JSON.parse(String(posts()[0].init!.body));
    expect(payload.alert_id).toBe("ALRT-5000");
    expect(payload.rule_id).toBe("SAN-PEP-061");
    expect(payload.created_at).toBe("2026-10-02T21:30:00Z"); // 03:00 IST is the previous day in UTC
  });

  it("does not overwrite an ID typed before the suggestion arrives", async () => {
    let release!: () => void;
    const gate = new Promise<void>((r) => (release = r));
    calls = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        calls.push({ url, init });
        if (url.includes("/alerts/form-options")) await gate;
        const body = url.includes("/alerts/form-options") ? FORM_OPTIONS : listBody([], 0);
        return { ok: true, status: 200, json: async () => body } as Response;
      }),
    );
    render(
      <MemoryRouter>
        <Alerts />
      </MemoryRouter>,
    );
    await screen.findByText("No alerts found");
    await userEvent.click(screen.getByRole("button", { name: /Add alert/ }));
    const modal = within(screen.getByRole("dialog"));
    await userEvent.type(modal.getByLabelText("Alert ID"), "ALRT-7777");
    release();
    await waitFor(() => expect((modal.getByRole("button", { name: "Rule" }) as HTMLButtonElement).disabled).toBe(false));
    expect((modal.getByLabelText("Alert ID") as HTMLInputElement).value).toBe("ALRT-7777");
  });

  it("shows a cleared ID as an error instead of generating another", async () => {
    const modal = await openForm();
    await userEvent.clear(modal.getByLabelText("Alert ID"));
    expect(modal.getByText("Alert ID is required.")).toBeTruthy();
    expect((modal.getByLabelText("Alert ID") as HTMLInputElement).value).toBe("");
    await userEvent.click(modal.getByRole("button", { name: /Create alert/ }));
    expect(posts().length).toBe(0);
    expect((modal.getByLabelText("Alert ID") as HTMLInputElement).value).toBe("");
  });

  it("flags an ID already in use as it is typed, without sending", async () => {
    const modal = await openForm();
    const id = modal.getByLabelText("Alert ID");
    await userEvent.clear(id);
    await userEvent.type(id, "ALRT-0090");
    expect(modal.getByText("ALRT-0090 is already used by another alert. Choose a different ID.")).toBeTruthy();
    await userEvent.click(modal.getByRole("button", { name: /Create alert/ }));
    expect(posts().length).toBe(0);
  });

  it("shows the duplicate message on 409 and keeps the modal open", async () => {
    // the server remains the final authority, e.g. for an ID created meanwhile
    const modal = await openForm(() => [409, { detail: "alert_id 'ALRT-0091' already exists" }]);
    await pick(modal, "Rule", /TM-VEL-014/);
    await pick(modal, "Customer", /CUST-0101/);
    detected(modal, "2026-10-03", "14:30");
    await userEvent.click(modal.getByRole("button", { name: /Create alert/ }));
    expect(await screen.findByText("Alert already exists")).toBeTruthy();
    expect(screen.getByText(/An alert with this Alert ID already exists/)).toBeTruthy();
    expect(modal.getByText("This Alert ID already exists. Choose a different ID.")).toBeTruthy();
    expect(screen.getByRole("dialog")).toBeTruthy();
  });

  it("shows the server's field message when it refuses a value", async () => {
    const modal = await openForm(() => [
      422,
      { detail: [{ loc: ["body", "customer_ref"], msg: "Unknown customer 'CUST-0101'. Choose an existing customer.", type: "value_error" }] },
    ]);
    await pick(modal, "Rule", /TM-VEL-014/);
    await pick(modal, "Customer", /CUST-0101/);
    detected(modal, "2026-10-03", "14:30");
    await userEvent.click(modal.getByRole("button", { name: /Create alert/ }));
    expect(await modal.findByText("Unknown customer 'CUST-0101'. Choose an existing customer.")).toBeTruthy();
  });

  it("defaults Detected At to the current date and time in IST, and it can be changed", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-10-05T09:15:00Z")); // 14:45 IST
    try {
      const modal = await openForm();
      expect((modal.getByLabelText("Detected date (IST)") as HTMLInputElement).value).toBe("2026-10-05");
      expect((modal.getByLabelText("Detected time (IST)") as HTMLInputElement).value).toBe("14:45");
      detected(modal, "2026-10-03", "14:30");
      expect((modal.getByLabelText("Detected time (IST)") as HTMLInputElement).value).toBe("14:30");
      expect(modal.getByTestId("utc-preview").textContent).toMatch(/2026-10-03T09:00:00Z/);
    } finally {
      vi.useRealTimers();
    }
  });

  it("shows the programme code only, never the programme name", async () => {
    const modal = await openForm();
    await pick(modal, "Customer", /CUST-0101/);
    expect(modal.getByTestId("programme").textContent).toBe("P001");
    expect(modal.queryByText(/Programme Alpha/)).toBeNull();
  });

  it("blocks submission with a message on each missing value", async () => {
    const modal = await openForm();
    await userEvent.clear(modal.getByLabelText("Alert ID"));
    detected(modal, "", "");
    await userEvent.click(modal.getByRole("button", { name: /Create alert/ }));
    expect(await screen.findByText(/check the highlighted fields/)).toBeTruthy();
    for (const m of ["Alert ID is required.", "Choose a rule.", "Choose a customer.",
      "Enter the date and time the alert was detected, in IST."])
      expect(modal.getByText(m)).toBeTruthy();
    expect(posts().length).toBe(0);
  });
});

describe("detected times in IST", () => {
  it("converts an IST date and time to the stored UTC timestamp", () => {
    expect(istToUtcIso("2026-10-03", "14:30")).toBe("2026-10-03T09:00:00Z");
    expect(istToUtcIso("2026-10-03", "03:00")).toBe("2026-10-02T21:30:00Z");
    expect(istToUtcIso("2026-12-31", "23:59")).toBe("2026-12-31T18:29:00Z");
    expect(istToUtcIso("2026-02-31", "10:00")).toBeNull();
    expect(istToUtcIso("2026-10-03", "")).toBeNull();
    expect(istToUtcIso("", "14:30")).toBeNull();
  });

  it("shows a stored UTC time in IST, labelled", () => {
    expect(fmtDetected("2026-10-03T09:00:00Z")).toBe("03 Oct 2026, 14:30 IST");
    expect(fmtDetected("2026-10-02T21:30:00Z")).toBe("03 Oct 2026, 03:00 IST");
    expect(fmtDetected(istToUtcIso("2026-10-03", "14:30"))).toBe("03 Oct 2026, 14:30 IST");
    expect(fmtDetected(null)).toBe("—");
  });

  it("the alerts table shows the detected time in IST", async () => {
    mockFetch(() => [200, listBody([alert("ALRT-0001", { created_at: "2026-09-22T20:00:00Z" })], 1)]);
    render(
      <MemoryRouter>
        <Alerts />
      </MemoryRouter>,
    );
    const row = (await screen.findByText("ALRT-0001")).closest("tr")!;
    expect(within(row).getByText("23 Sep 2026")).toBeTruthy(); // 20:00 UTC is the next day in IST
    expect(within(row).getByText("01:30 IST")).toBeTruthy();
    expect(row.textContent).not.toMatch(/UTC/);
  });

  it("the alert page shows the detected time in IST", async () => {
    mockFetch((_url, init) => (init?.method === "POST" ? [200, INVESTIGATION] : [200, alert("ALRT-0001")]));
    render(
      <MemoryRouter initialEntries={["/alerts/ALRT-0001"]}>
        <Routes>
          <Route path="/alerts/:alertId" element={<AlertDetail />} />
        </Routes>
      </MemoryRouter>,
    );
    expect(await screen.findByText("22 Sep 2026, 11:30 IST")).toBeTruthy();
  });

  it("the cluster member table shows detected times in IST", async () => {
    mockFetch((url) => {
      if (url.endsWith("/clusters")) return [200, CLUSTERS];
      const m = /\/alerts\/(ALRT-\d+)$/.exec(url);
      if (m) return [200, alert(m[1])];
      return [404, { detail: "not mocked" }];
    });
    render(
      <MemoryRouter>
        <Clusters />
      </MemoryRouter>,
    );
    expect((await screen.findAllByText("22 Sep 2026, 11:30 IST")).length).toBe(2);
  });
});

describe("alert detail and investigation", () => {
  const renderDetail = () =>
    render(
      <MemoryRouter initialEntries={["/alerts/ALRT-0001"]}>
        <Routes>
          <Route path="/alerts/:alertId" element={<AlertDetail />} />
        </Routes>
      </MemoryRouter>,
    );

  it("loads the alert and shows a 404 message for an unknown id", async () => {
    mockFetch(() => [404, { detail: "no alert with alert_id 'ALRT-0001'" }]);
    renderDetail();
    expect(await screen.findByText("Not found.")).toBeTruthy();
  });

  it("runs the investigation automatically when the alert opens", async () => {
    mockFetch((_url, init) => (init?.method === "POST" ? [200, INVESTIGATION] : [200, alert("ALRT-0001")]));
    renderDetail();
    expect(await screen.findByRole("heading", { level: 1, name: "Unusual Transaction Velocity" })).toBeTruthy();
    const post = calls.find((c) => c.init?.method === "POST" && c.url.endsWith("/investigate"))!;
    expect(JSON.parse(String(post.init!.body))).toEqual({ alert: alert("ALRT-0001") });
  });

  it("renders evidence with readable labels, missing information, provenance and outcomes", async () => {
    mockFetch((_url, init) => (init?.method === "POST" ? [200, INVESTIGATION] : [200, alert("ALRT-0001")]));
    renderDetail();
    expect(await screen.findByRole("heading", { level: 1, name: "Unusual Transaction Velocity" })).toBeTruthy();
    expect(screen.getByText("1 FOUND")).toBeTruthy();
    expect(screen.getByText("1 MISSING")).toBeTruthy();
    // evidence keys are presented as readable labels; values are unchanged
    expect(screen.getByText("KYB Status")).toBeTruthy();
    expect(screen.getByText("complete")).toBeTruthy();
    expect(screen.getAllByText("MISSING").length).toBeGreaterThan(0);
    expect(screen.getByText("⚠ Source of Funds")).toBeTruthy();
    // provenance is available but secondary
    expect(screen.getByText(/How this evidence was collected/)).toBeTruthy();
    expect(screen.getByText(/Tool calls · 1/)).toBeTruthy();
    expect(screen.getByText("field kyb_status")).toBeTruthy();
    expect(screen.getByText("Escalate to MLRO")).toBeTruthy();
  });

  it("does not render a missing value as false or failed", async () => {
    mockFetch((_url, init) => (init?.method === "POST" ? [200, INVESTIGATION] : [200, alert("ALRT-0001")]));
    renderDetail();
    await screen.findByRole("heading", { level: 1, name: "Unusual Transaction Velocity" });
    const item = screen.getByText("Source of Funds").closest("details")!;
    expect(within(item).queryByText(/false/i)).toBeNull();
    expect(within(item).queryByText(/failed/i)).toBeNull();
    expect(within(item).getByText("Not available in the collected evidence")).toBeTruthy();
    expect(within(item).getByText("MISSING")).toBeTruthy();
  });
});

describe("AI analyst summary", () => {
  const renderDetail = () =>
    render(
      <MemoryRouter initialEntries={["/alerts/ALRT-0001"]}>
        <Routes>
          <Route path="/alerts/:alertId" element={<AlertDetail />} />
        </Routes>
      </MemoryRouter>,
    );

  const withSummary = (summary: [number, unknown]) =>
    mockFetch((url, init) => {
      if (init?.method === "POST" && url.endsWith("/investigate/summary")) return summary;
      if (init?.method === "POST") return [200, INVESTIGATION];
      return [200, alert("ALRT-0001")];
    });

  it("is requested automatically, and only after the investigation", async () => {
    withSummary([200, AI_SUMMARY]);
    renderDetail();
    const card = within(await screen.findByLabelText("AI Analyst Summary"));
    await card.findByText(/An alert for unusual transaction velocity has been raised/);
    const inv = calls.findIndex((c) => c.url.endsWith("/investigate"));
    const sum = calls.findIndex((c) => c.url.endsWith("/investigate/summary"));
    expect(inv).toBeGreaterThanOrEqual(0);
    expect(sum).toBeGreaterThan(inv);
    expect(JSON.parse(String(calls[sum].init!.body))).toEqual({ alert: alert("ALRT-0001") });
  });

  it("renders the real summary, labelled AI-generated, with citations and no decision", async () => {
    withSummary([200, AI_SUMMARY]);
    renderDetail();
    const node = await screen.findByLabelText("AI Analyst Summary");
    const card = within(node);
    // the narrative starts with the alert type and names the missing item itself
    expect(await card.findByText(/An alert for unusual transaction velocity has been raised\. The KYB status is recorded as complete\. Source of funds is not available/)).toBeTruthy();
    expect(card.getByText("AI-generated")).toBeTruthy();
    expect(card.getByText(/final decision remains with the analyst/)).toBeTruthy();
    for (const h of ["Key findings", "Analyst attention"]) expect(card.getByText(h)).toBeTruthy();
    // missing items are in the narrative, not repeated as a separate block
    expect(card.queryByText("Missing information")).toBeNull();
    expect(card.getByText("KYB status: complete")).toBeTruthy();
    expect(card.getByText("Source of funds: not available")).toBeTruthy();
    expect(card.getAllByText("Source of Funds").length).toBeGreaterThan(0); // citation chip
    expect(card.getByText(/Generated by model-from-env/)).toBeTruthy();
    expect(node.textContent).not.toMatch(/confidence|recommend/i);
  });

  it("shows a MISSING basis item as missing, not false or failed", async () => {
    withSummary([200, AI_SUMMARY]);
    renderDetail();
    const card = within(await screen.findByLabelText("AI Analyst Summary"));
    await userEvent.click(await card.findByText(/Evidence basis · 2 cited items/));
    const row = card
      .getAllByText("Source of Funds")
      .map((n) => n.closest("tr"))
      .find(Boolean)!;
    expect(within(row).getByText("MISSING")).toBeTruthy();
    expect(within(row).getByText("Not available")).toBeTruthy();
    expect(within(row).queryByText(/false|failed/i)).toBeNull();
  });

  it("shows the unavailable state and leaves the investigation intact", async () => {
    withSummary([
      200,
      {
        ...AI_SUMMARY,
        ai_summary: {
          ...AI_SUMMARY.ai_summary,
          status: "unavailable",
          generated_by: null,
          summary: null,
          summary_evidence_keys: [],
          key_findings: [],
          missing_information: [],
          analyst_attention: [],
          evidence_basis: [],
          reason: "AI summary unavailable: OPENAI_API_KEY is not configured",
        },
      },
    ]);
    renderDetail();
    const card = within(await screen.findByLabelText("AI Analyst Summary"));
    expect(await card.findByText("AI summary unavailable")).toBeTruthy();
    expect(card.getByText(/OPENAI_API_KEY is not configured/)).toBeTruthy();
    expect(card.getByText(/deterministic investigation results are still available below/)).toBeTruthy();
    expect(card.queryByText("Key findings")).toBeNull();
    expect(screen.getByText("1 FOUND")).toBeTruthy();
    expect(screen.getByText("⚠ Source of Funds")).toBeTruthy();
  });

  it("keeps the investigation visible when the summary request itself fails", async () => {
    withSummary([500, { detail: "internal error" }]);
    renderDetail();
    const card = within(await screen.findByLabelText("AI Analyst Summary"));
    expect(await card.findByText("AI summary unavailable")).toBeTruthy();
    expect(card.getByText("The monitoring API reported an error.")).toBeTruthy();
    expect(screen.getByRole("heading", { level: 1, name: "Unusual Transaction Velocity" })).toBeTruthy();
    expect(screen.getByText("1 MISSING")).toBeTruthy();
  });
});

const ROUTING_OPTIONS = {
  destinations: [
    { id: "mlro", label: "MLRO / Nominated Officer", description: "AML suspicion requiring specialist judgement" },
    { id: "sanctions_analyst", label: "Sanctions Analyst", description: "Screening/PEP result requiring manual review" },
    { id: "monitoring_engineering", label: "Monitoring Engineering", description: "Monitoring rule/pattern appears mistuned" },
    { id: "programme_operations", label: "Programme Operations", description: "Technical or programme/partner-specific issue" },
    { id: "kyb_cdd", label: "KYB / CDD Team", description: "Incomplete customer/KYB information" },
  ],
  outcomes: [{ id: "no_action_required", label: "No Further Action", description: "Close the alert without routing it" }],
};
const AI_OFF = {
  ...AI_SUMMARY,
  ai_summary: {
    ...AI_SUMMARY.ai_summary,
    status: "unavailable",
    summary: null,
    key_findings: [],
    missing_information: [],
    analyst_attention: [],
    summary_evidence_keys: [],
    evidence_basis: [],
    reason: "no key",
  },
};

describe("routing", () => {
  const renderDetail = () =>
    render(
      <MemoryRouter initialEntries={["/alerts/ALRT-0001"]}>
        <Routes>
          <Route path="/alerts/:alertId" element={<AlertDetail />} />
        </Routes>
      </MemoryRouter>,
    );

  function backend(
    opts: {
      routing?: Record<string, unknown>;
      investigation?: unknown;
      put?: (body: Record<string, string>) => [number, unknown];
    } = {},
  ) {
    mockFetch((url, init) => {
      if (url.endsWith("/routing") && init?.method === "PUT") {
        const body = JSON.parse(String(init.body));
        return opts.put
          ? opts.put(body)
          : [
              200,
              {
                alert_id: "ALRT-0001",
                destination: body.destination ?? null,
                outcome: body.outcome ?? null,
                routed_at: "2026-10-05T10:12:03Z",
              },
            ];
      }
      if (url.endsWith("/routing")) return [200, { ...ROUTING_OPTIONS, routing: opts.routing ?? {} }];
      if (url.endsWith("/investigate/summary")) return [200, AI_OFF];
      if (init?.method === "POST") return [200, opts.investigation ?? INVESTIGATION];
      return [200, alert("ALRT-0001")];
    });
  }

  const picker = () => screen.findByRole("button", { name: "Routing destination" });
  const option = async (name: RegExp) => {
    await userEvent.click(await picker());
    return screen.getByRole("option", { name });
  };
  const choose = async (name: RegExp) => userEvent.click(await option(name));

  it("choosing a destination does not route it; Route alert records it once", async () => {
    backend();
    renderDetail();
    const select = await picker();
    const status = within(screen.getByLabelText("Routing decision"));
    expect(status.getByText("Not routed")).toBeTruthy();
    const header = screen.getByText("Routing").closest("div")!;
    expect(within(header).getByText("Not routed")).toBeTruthy();

    expect(select.getAttribute("aria-expanded")).toBe("false");
    await choose(/^Sanctions Analyst/);
    expect(screen.queryByRole("listbox")).toBeNull(); // closes after choosing
    expect(calls.some((c) => c.init?.method === "PUT")).toBe(false);
    expect(screen.getByText(/Nothing is recorded until you press the button/)).toBeTruthy();

    await userEvent.click(screen.getByRole("button", { name: "Route alert" }));
    expect(await screen.findByText(/Alert routed to Sanctions Analyst\./)).toBeTruthy();
    const puts = calls.filter((c) => c.init?.method === "PUT");
    expect(puts.length).toBe(1);
    expect(puts[0].url.endsWith("/alerts/ALRT-0001/routing")).toBe(true);
    expect(JSON.parse(String(puts[0].init!.body))).toEqual({ destination: "sanctions_analyst" });
    expect(status.getByText("Routed to")).toBeTruthy();
    expect(status.getByText("Sanctions Analyst")).toBeTruthy();
    expect(status.getByText("Routed")).toBeTruthy();
    expect(within(header).getByText("Routed · Sanctions Analyst")).toBeTruthy();
    // the options stay on screen, showing the current choice
    expect((await picker()).textContent).toMatch(/Sanctions Analyst/);
    expect((screen.getByRole("button", { name: "Update routing" }) as HTMLButtonElement).disabled).toBe(true);
    // routing never re-runs the investigation or the AI summary
    expect(calls.filter((c) => c.url.endsWith("/investigate")).length).toBe(1);
    expect(calls.filter((c) => c.url.endsWith("/investigate/summary")).length).toBe(1);
  });

  it("records No Further Action as an outcome when the playbook permits it", async () => {
    backend();
    renderDetail();
    const nfa = await option(/^No Further Action/);
    expect(nfa.getAttribute("aria-disabled")).toBeNull();
    await userEvent.click(nfa);
    await userEvent.click(screen.getByRole("button", { name: "Record outcome" }));
    expect(await screen.findByText(/Outcome recorded: No Further Action\./)).toBeTruthy();
    const put = calls.find((c) => c.init?.method === "PUT")!;
    expect(JSON.parse(String(put.init!.body))).toEqual({ outcome: "no_action_required" });
    expect(within(screen.getByLabelText("Routing decision")).getByText("Closed")).toBeTruthy();
  });

  it("disables No Further Action, with the playbook reason, when it is not permitted", async () => {
    backend({ investigation: { ...INVESTIGATION, allowed_outcomes: ["escalate_to_sanctions_analyst"] } });
    renderDetail();
    const nfa = await option(/^No Further Action/);
    expect(nfa.getAttribute("aria-disabled")).toBe("true");
    expect(nfa.textContent).toMatch(/Not permitted by this playbook/);
    await userEvent.click(nfa); // a disabled option cannot be chosen
    expect((await picker()).textContent).toMatch(/Select a destination/);
    expect(screen.getByRole("listbox")).toBeTruthy(); // stays open after a disabled click
    expect(screen.getByText(/No Further Action is not available: the .* playbook does not permit it\./)).toBeTruthy();
    // destinations stay available
    expect(screen.getByRole("option", { name: /^MLRO \/ Nominated Officer/ }).getAttribute("aria-disabled")).toBeNull();
  });

  it("shows an existing decision on load, keeps it selected, and can change it in place", async () => {
    backend({
      routing: {
        "ALRT-0001": { alert_id: "ALRT-0001", destination: "mlro", outcome: null, routed_at: "2026-10-05T09:00:00Z" },
      },
    });
    renderDetail();
    const select = await picker();
    const status = within(screen.getByLabelText("Routing decision"));
    await waitFor(() => expect(status.getByText("MLRO / Nominated Officer")).toBeTruthy());
    expect(status.getByText("05 Oct 2026, 09:00 UTC")).toBeTruthy();
    expect(select.textContent).toMatch(/MLRO \/ Nominated Officer/);
    await choose(/^KYB \/ CDD Team/);
    expect(calls.some((c) => c.init?.method === "PUT")).toBe(false);
    await userEvent.click(screen.getByRole("button", { name: "Update routing" }));
    expect(await screen.findByText(/Alert routed to KYB \/ CDD Team\./)).toBeTruthy();
  });

  it("shows the server reason when a routing decision is refused", async () => {
    backend({ put: () => [409, { detail: "outcome 'no_action_required' is not permitted by playbook 'x'" }] });
    renderDetail();
    await choose(/^KYB \/ CDD Team/);
    await userEvent.click(screen.getByRole("button", { name: "Route alert" }));
    expect(await screen.findByText(/is not permitted by playbook/)).toBeTruthy();
    expect(within(screen.getByLabelText("Routing decision")).getByText("Not routed")).toBeTruthy();
  });

  it("opens its list below the button and can be driven by keyboard", async () => {
    backend();
    renderDetail();
    const btn = await picker();
    btn.focus();
    await userEvent.keyboard("{ArrowDown}");
    const list = screen.getByRole("listbox");
    // the list is rendered after the button, positioned under it (top: 100%)
    expect(btn.compareDocumentPosition(list) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(list.className).toBe("dd-list");
    await userEvent.keyboard("{ArrowDown}{Enter}");
    expect(btn.textContent).toMatch(/Sanctions Analyst/);
    expect(calls.some((c) => c.init?.method === "PUT")).toBe(false);
    await userEvent.click(btn);
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("listbox")).toBeNull();
  });

  it("shows each alert destination on the dashboard, never blank", async () => {
    mockFetch((url) => {
      if (url.endsWith("/routing"))
        return [
          200,
          {
            ...ROUTING_OPTIONS,
            routing: {
              "ALRT-0001": { alert_id: "ALRT-0001", destination: "mlro", outcome: null, routed_at: "2026-10-05T09:00:00Z" },
              "ALRT-0002": {
                alert_id: "ALRT-0002",
                destination: null,
                outcome: "no_action_required",
                routed_at: "2026-10-05T09:05:00Z",
              },
            },
          },
        ];
      return [200, listBody([alert("ALRT-0001"), alert("ALRT-0002"), alert("ALRT-0003")], 3)];
    });
    render(
      <MemoryRouter>
        <Alerts />
      </MemoryRouter>,
    );
    expect(await screen.findByText("Destination")).toBeTruthy();
    const row = async (id: string) => (await screen.findByText(id)).closest("tr")!;
    await waitFor(async () => expect(within(await row("ALRT-0001")).getByText("MLRO / Nominated Officer")).toBeTruthy());
    expect(within(await row("ALRT-0002")).getByText("No Further Action")).toBeTruthy();
    expect(within(await row("ALRT-0003")).getByText("Not routed")).toBeTruthy();
  });
});

describe("clusters", () => {
  it("renders clusters and their members", async () => {
    mockFetch(() => [200, CLUSTERS]);
    render(
      <MemoryRouter>
        <Clusters />
      </MemoryRouter>,
    );
    expect(await screen.findByText("C001")).toBeTruthy();
    expect(screen.getByText("Repeated Velocity Pattern")).toBeTruthy();
    expect(screen.getByText("ALRT-0002")).toBeTruthy();
    expect(screen.getByText(/not a durable identifier/)).toBeTruthy();
  });

  it("shows each member alert with that alert's own customer, not customer_refs[i]", async () => {
    // customer_refs is the sorted set of distinct customers: shorter than
    // alert_ids and not aligned with it. Positional pairing would show
    // ALRT-UI-0001 -> CUST-0102 and leave the last members blank.
    const owner: Record<string, string> = {
      "ALRT-0001": "CUST-0101",
      "ALRT-UI-0001": "CUST-0101",
      "ALRT-0002": "CUST-0103",
      "ALRT-0004": "CUST-0102",
    };
    const cluster = {
      ...CLUSTERS.clusters[0],
      alert_ids: Object.keys(owner),
      customer_refs: ["CUST-0101", "CUST-0102", "CUST-0103"],
    };
    mockFetch((url) => {
      const m = url.match(/\/alerts\/([^/?]+)$/);
      if (m) {
        const id = decodeURIComponent(m[1]);
        return id in owner
          ? [200, alert(id, { customer_ref: owner[id] })]
          : [404, { detail: `no alert with alert_id '${id}'` }];
      }
      return [200, { ...CLUSTERS, clusters: [cluster] }];
    });
    render(
      <MemoryRouter>
        <Clusters />
      </MemoryRouter>,
    );

    for (const [id, customer] of Object.entries(owner)) {
      const row = (await screen.findByText(id)).closest("tr")!;
      await waitFor(() => expect(within(row).getByText(customer)).toBeTruthy());
    }
    for (const id of Object.keys(owner)) {
      expect(calls.some((c) => c.url.endsWith(`/alerts/${id}`))).toBe(true);
    }
  });

  it("shows a dash, not another alert's customer, when a member cannot be read", async () => {
    const cluster = { ...CLUSTERS.clusters[0], alert_ids: ["ALRT-0001", "ALRT-GONE"] };
    mockFetch((url) => {
      if (url.endsWith("/alerts/ALRT-0001")) return [200, alert("ALRT-0001")];
      if (url.endsWith("/alerts/ALRT-GONE")) return [404, { detail: "no alert" }];
      return [200, { ...CLUSTERS, clusters: [cluster] }];
    });
    render(
      <MemoryRouter>
        <Clusters />
      </MemoryRouter>,
    );
    const gone = (await screen.findByText("ALRT-GONE")).closest("tr")!;
    await waitFor(() => expect(within(gone).getByText("—")).toBeTruthy());
    expect(within(gone).queryByText("CUST-0102")).toBeNull();
  });

  it("shows an empty state when no clusters form", async () => {
    mockFetch(() => [
      200,
      { clusters: [], membership: {}, alerts_considered: 0, alerts_clustered: 0, note: "" },
    ]);
    render(
      <MemoryRouter>
        <Clusters />
      </MemoryRouter>,
    );
    expect(await screen.findByText("No clusters found")).toBeTruthy();
  });
});

describe("cluster page: AI summary and cluster routing", () => {
  const MEMBERS = ["ALRT-0001", "ALRT-0002"];
  const CL = { ...CLUSTERS, clusters: [{ ...CLUSTERS.clusters[0], alert_ids: MEMBERS }] };
  const KEY = "repeated_velocity:ALRT-0001,ALRT-0002";
  const SUMMARY = {
    cluster_type: "repeated_velocity",
    label: "Repeated Velocity Pattern",
    alert_ids: MEMBERS,
    ai_summary: {
      status: "generated",
      label: "AI-generated",
      generated_by: "llm",
      model: "model-from-env",
      summary: "This Repeated Velocity Pattern contains 2 alerts across 2 customers in P001.",
      summary_fields: ["cluster.alert_count", "cluster.programme_refs"],
      criteria_met: [{ text: "Alerts: 2 against a threshold of 5 (met).", fields: ["criteria.min_alerts"] }],
      characteristics: [{ text: "All alerts come from TM-VEL-014.", fields: ["cluster.rule_ids"] }],
      reason: null,
    },
  };
  const OPTIONS = {
    destinations: [
      { id: "mlro", label: "MLRO / Nominated Officer", description: "AML suspicion requiring specialist judgement" },
      { id: "kyb_cdd", label: "KYB / CDD Team", description: "Incomplete customer/KYB information" },
    ],
    outcomes: [{ id: "no_action_required", label: "No Further Action", description: "Close the alert without routing it" }],
  };

  function backend(opts: { clusters?: unknown[]; investigation?: unknown; summary?: [number, unknown] } = {}) {
    const state = { routing: {} as Record<string, unknown>, clusters: (opts.clusters ?? []) as unknown[] };
    mockFetch((url, init) => {
      if (init?.method === "PUT" && url.endsWith("/clusters/routing")) {
        const body = JSON.parse(String(init.body));
        const rec = { key: KEY, cluster_type: "repeated_velocity", label: "Repeated Velocity Pattern",
          alert_ids: [...body.alert_ids].sort(), destination: body.destination ?? null, outcome: body.outcome ?? null,
          routed_at: "2026-10-05T11:00:00Z" };
        state.clusters = [rec];
        for (const a of body.alert_ids)
          state.routing[a] = { alert_id: a, destination: rec.destination, outcome: rec.outcome, routed_at: rec.routed_at,
            source: "cluster", cluster_type: rec.cluster_type, cluster_label: rec.label };
        return [200, rec];
      }
      if (url.endsWith("/routing")) return [200, { ...OPTIONS, routing: state.routing, clusters: state.clusters }];
      if (url.endsWith("/clusters/summary")) return opts.summary ?? [200, SUMMARY];
      if (url.endsWith("/clusters")) return [200, CL];
      const m = url.match(/\/alerts\/([^/?]+)$/);
      if (m) return [200, alert(decodeURIComponent(m[1]))];
      if (init?.method === "POST") return [200, opts.investigation ?? INVESTIGATION];
      return [200, listBody([], 0)];
    });
    return state;
  }
  const renderClusters = () =>
    render(
      <MemoryRouter>
        <Clusters />
      </MemoryRouter>,
    );
  const picker = () => screen.findByRole("button", { name: "Routing destination" });

  it("shows the AI cluster summary from the real endpoint, and no evidence-gaps section", async () => {
    backend();
    renderClusters();
    const card = within(await screen.findByLabelText("AI Cluster Summary"));
    expect(await card.findByText(/contains 2 alerts across 2 customers in P001/)).toBeTruthy();
    expect(card.getByText("AI-generated")).toBeTruthy();
    expect(card.getByText("Criteria met")).toBeTruthy();
    expect(card.getByText("Alerts: 2 against a threshold of 5 (met).")).toBeTruthy();
    expect(card.getByText(/Generated by model-from-env/)).toBeTruthy();
    const post = calls.find((c) => c.url.endsWith("/clusters/summary"))!;
    expect(JSON.parse(String(post.init!.body))).toEqual({ alert_ids: MEMBERS });
    expect(screen.queryByText(/Evidence gaps across members/i)).toBeNull();
    expect(screen.getByText("Why these alerts were grouped")).toBeTruthy();
  });

  it("keeps the page working when the AI cluster summary is unavailable", async () => {
    backend({ summary: [500, { detail: "boom" }] });
    renderClusters();
    const card = within(await screen.findByLabelText("AI Cluster Summary"));
    expect(await card.findByText("AI summary unavailable")).toBeTruthy();
    expect(screen.getByText("Member alerts · 2")).toBeTruthy();
  });

  it("routes the cluster only when Route cluster is pressed, and members then show via cluster", async () => {
    backend();
    renderClusters();
    await userEvent.click(await picker());
    await userEvent.click(screen.getByRole("option", { name: /^MLRO \/ Nominated Officer/ }));
    expect(calls.some((c) => c.init?.method === "PUT")).toBe(false);
    await userEvent.click(screen.getByRole("button", { name: "Route cluster" }));
    expect(await screen.findByText(/Cluster routed to MLRO \/ Nominated Officer\./)).toBeTruthy();
    const put = calls.find((c) => c.init?.method === "PUT")!;
    expect(put.url.endsWith("/clusters/routing")).toBe(true);
    expect(JSON.parse(String(put.init!.body))).toEqual({ alert_ids: MEMBERS, destination: "mlro" });
    for (const id of MEMBERS) {
      const row = (await screen.findByText(id)).closest("tr")!;
      await waitFor(() => expect(within(row).getByText("via cluster")).toBeTruthy());
      expect(within(row).getByText("MLRO / Nominated Officer")).toBeTruthy();
    }
    // no member investigation is changed: routing never re-runs one
    const investigations = calls.filter((c) => c.url.endsWith("/investigate")).length;
    expect(investigations).toBe(MEMBERS.length);
  });

  it("disables No Further Action unless every member's playbook permits it", async () => {
    backend({ investigation: { ...INVESTIGATION, allowed_outcomes: ["escalate_to_sanctions_analyst"] } });
    renderClusters();
    await waitFor(() => expect(calls.filter((c) => c.url.endsWith("/investigate")).length).toBe(2));
    await userEvent.click(await picker());
    const nfa = screen.getByRole("option", { name: /^No Further Action/ });
    await waitFor(() => expect(nfa.textContent).toMatch(/Not permitted by every member's playbook/));
    expect(nfa.getAttribute("aria-disabled")).toBe("true");
  });

  it("treats a cluster whose membership changed as not routed", async () => {
    backend({
      clusters: [{ key: "repeated_velocity:ALRT-0001", cluster_type: "repeated_velocity", label: "Repeated Velocity Pattern",
        alert_ids: ["ALRT-0001"], destination: "mlro", outcome: null, routed_at: "2026-10-05T09:00:00Z" }],
    });
    renderClusters();
    const status = within(await screen.findByLabelText("Routing decision"));
    expect(status.getByText("Not routed")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Route cluster" })).toBeTruthy();
  });

  it("shows on the alert page and dashboard that routing came from the cluster", async () => {
    const via = { alert_id: "ALRT-0001", destination: "mlro", outcome: null, routed_at: "2026-10-05T11:00:00Z",
      source: "cluster", cluster_type: "repeated_velocity", cluster_label: "Repeated Velocity Pattern" };
    mockFetch((url, init) => {
      if (url.endsWith("/routing")) return [200, { ...OPTIONS, routing: { "ALRT-0001": via }, clusters: [] }];
      if (url.endsWith("/investigate/summary")) return [200, { ...AI_SUMMARY, ai_summary: { ...AI_SUMMARY.ai_summary, status: "unavailable", reason: "x" } }];
      if (init?.method === "POST") return [200, INVESTIGATION];
      if (url.includes("/alerts?")) return [200, listBody([alert("ALRT-0001"), alert("ALRT-0002")], 2)];
      return [200, alert("ALRT-0001")];
    });
    const { unmount } = render(
      <MemoryRouter initialEntries={["/alerts/ALRT-0001"]}>
        <Routes>
          <Route path="/alerts/:alertId" element={<AlertDetail />} />
        </Routes>
      </MemoryRouter>,
    );
    const header = (await screen.findByText("Routing")).closest("div")!;
    await waitFor(() => expect(within(header).getByText("Routed via cluster · MLRO / Nominated Officer")).toBeTruthy());
    expect(within(screen.getByLabelText("Routing decision")).getByText("Via cluster · Repeated Velocity Pattern")).toBeTruthy();
    expect(screen.getByText(/takes precedence over the cluster routing/)).toBeTruthy();
    unmount();

    render(
      <MemoryRouter>
        <Alerts />
      </MemoryRouter>,
    );
    const row = (await screen.findByText("ALRT-0001")).closest("tr")!;
    await waitFor(() => expect(within(row).getByText("via cluster")).toBeTruthy());
    const other = (await screen.findByText("ALRT-0002")).closest("tr")!;
    expect(within(other).getByText("Not routed")).toBeTruthy();
  });
});

describe("AI summary cache", () => {
  const renderDetail = () =>
    render(
      <MemoryRouter initialEntries={["/alerts/ALRT-0001"]}>
        <Routes>
          <Route path="/alerts/:alertId" element={<AlertDetail />} />
        </Routes>
      </MemoryRouter>,
    );

  it("opening an alert asks for the cached summary; Regenerate explicitly bypasses the cache", async () => {
    const cached = {
      ...AI_SUMMARY,
      ai_summary: { ...AI_SUMMARY.ai_summary, cached: true, generated_at: "2026-10-05T07:43:00Z" },
    };
    mockFetch((url, init) => {
      if (url.includes("/investigate/summary")) return [200, cached];
      if (init?.method === "POST") return [200, INVESTIGATION];
      return [200, alert("ALRT-0001")];
    });
    renderDetail();
    const card = within(await screen.findByLabelText("AI Analyst Summary"));
    expect(await card.findByText(/reused from the cache, no new model call/)).toBeTruthy();
    expect(card.getByText(/05 Oct 2026, 07:43 UTC/)).toBeTruthy();
    const first = calls.filter((c) => c.url.includes("/investigate/summary"));
    expect(first.length).toBe(1);
    expect(first[0].url).not.toMatch(/regenerate/);

    await userEvent.click(card.getByRole("button", { name: "Regenerate AI summary" }));
    await waitFor(() => expect(calls.filter((c) => c.url.includes("/investigate/summary")).length).toBe(2));
    expect(calls.filter((c) => c.url.includes("/investigate/summary"))[1].url).toMatch(/\?regenerate=true$/);
  });

  it("the cluster summary does the same", async () => {
    const CL = { ...CLUSTERS, clusters: [{ ...CLUSTERS.clusters[0], alert_ids: ["ALRT-0001", "ALRT-0002"] }] };
    const SUM = {
      cluster_type: "repeated_velocity",
      label: "Repeated Velocity Pattern",
      alert_ids: ["ALRT-0001", "ALRT-0002"],
      ai_summary: {
        status: "generated", label: "AI-generated", generated_by: "llm", model: "model-from-env",
        summary: "This Repeated Velocity Pattern contains 2 alerts.", summary_fields: ["cluster.alert_count"],
        criteria_met: [{ text: "Alerts: 2 against a minimum of 5 (met).", fields: ["criteria.min_alerts"] }],
        characteristics: [], reason: null, cached: true, generated_at: "2026-10-05T07:43:00Z",
      },
    };
    mockFetch((url, init) => {
      if (url.includes("/clusters/summary")) return [200, SUM];
      if (url.endsWith("/clusters")) return [200, CL];
      const m = url.match(/\/alerts\/([^/?]+)$/);
      if (m) return [200, alert(decodeURIComponent(m[1]))];
      if (init?.method === "POST") return [200, INVESTIGATION];
      return [200, listBody([], 0)];
    });
    render(
      <MemoryRouter>
        <Clusters />
      </MemoryRouter>,
    );
    const card = within(await screen.findByLabelText("AI Cluster Summary"));
    expect(await card.findByText(/reused from the cache, no new model call/)).toBeTruthy();
    expect(calls.filter((c) => c.url.includes("/clusters/summary"))[0].url).not.toMatch(/regenerate/);
    await userEvent.click(card.getByRole("button", { name: "Regenerate AI summary" }));
    await waitFor(() => expect(calls.filter((c) => c.url.includes("/clusters/summary")).length).toBe(2));
    expect(calls.filter((c) => c.url.includes("/clusters/summary"))[1].url).toMatch(/\?regenerate=true$/);
  });
});

