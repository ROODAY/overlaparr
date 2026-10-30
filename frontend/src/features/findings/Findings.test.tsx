import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useLocation } from "react-router-dom";
import type { ActionsBody, ActivityEntry, Finding } from "../../api/types";
import { Findings } from "../../pages/Findings";
import { dashboardFixture, findingFixture } from "../../test/fixtures";
import { renderWithProviders } from "../../test/render";
import { server } from "../../test/server";

const VIEW_KEY = "overlaparr.findings.view";

const FINDINGS: Finding[] = [
  findingFixture(),
  findingFixture({
    id: 2,
    title: "The Wire",
    year: 2002,
    kind: "series",
    instance: { id: "i-1", name: "Sonarr", type: "sonarr" },
    poster_url: "https://image.example/wire.jpg",
    badges: {
      coverage: "partial",
      covered_count: 3,
      relevant_count: 10,
      best_quality: "worse",
      edition: "unknown",
      has_number_matches: true,
    },
  }),
];

/** Every GET /findings request's query string, in order. */
let findingsQueries: URLSearchParams[] = [];

function LocationProbe() {
  const location = useLocation();
  return <output data-testid="location">{location.pathname + location.search}</output>;
}

function renderFindings(route = "/findings") {
  return renderWithProviders(
    <>
      <Findings />
      <LocationProbe />
    </>,
    { route },
  );
}

function logRow(overrides: Partial<ActivityEntry> = {}): ActivityEntry {
  return {
    id: 100,
    ts: "2026-10-27T10:00:00Z",
    actor: "ui",
    rule_id: null,
    rule_name: null,
    scan_run_id: null,
    instance_id: "i-2",
    instance_name: "Radarr",
    finding_id: 1,
    arr_id: 10,
    item_title: "Heat",
    action: "delete",
    scope: {},
    prior_state: {},
    result: "ok",
    message: null,
    undoable: true,
    undone_at: null,
    undo_of_id: null,
    can_undo: true,
    ...overrides,
  };
}

beforeEach(() => {
  localStorage.clear();
  findingsQueries = [];
  server.use(
    http.get("*/api/v1/dashboard", () => HttpResponse.json(dashboardFixture())),
    http.get("*/api/v1/findings", ({ request }) => {
      findingsQueries.push(new URL(request.url).searchParams);
      return HttpResponse.json({ items: FINDINGS, total: FINDINGS.length });
    }),
  );
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("Findings", () => {
  it("defaults to table", async () => {
    renderFindings();
    const table = await screen.findByRole("table");
    expect(within(table).getByRole("link", { name: /Heat/ })).toHaveAttribute(
      "href",
      "/findings/1",
    );
    expect(screen.getByRole("button", { name: "Table" })).toHaveAttribute("aria-pressed", "true");
    // Diff badges: coverage, quality, edition, match method, each saying what it measures.
    const heat = within(table).getByRole("link", { name: /Heat/ }).closest("tr")!;
    expect(heat).toHaveTextContent("Full coverage");
    expect(heat).toHaveTextContent("Better elsewhere");
    expect(heat).toHaveTextContent("Same edition");
    const wire = within(table).getByRole("link", { name: /The Wire/ }).closest("tr")!;
    expect(wire).toHaveTextContent("Partial 3/10");
    expect(wire).toHaveTextContent("Worse elsewhere");
    expect(wire).toHaveTextContent("Matched by number");
    expect(wire).not.toHaveTextContent("Different");
    expect(wire).not.toHaveTextContent("Edition unknown");
  });

  it("falls back to table when localStorage throws", async () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("denied");
    });
    renderFindings();
    expect(await screen.findByRole("table")).toBeInTheDocument();
  });

  it("toggle persists", async () => {
    const user = userEvent.setup();
    const first = renderFindings();
    await screen.findByRole("table");
    await user.click(screen.getByRole("button", { name: "Posters" }));
    expect(localStorage.getItem(VIEW_KEY)).toBe("posters");
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    first.unmount();

    renderFindings();
    const card = await screen.findByRole("article", { name: "The Wire" });
    expect(within(card).getByRole("img")).toHaveAttribute("src", "https://image.example/wire.jpg");
    // No poster URL: a neutral placeholder with the title.
    const heat = screen.getByRole("article", { name: "Heat" });
    expect(within(heat).queryByRole("img")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Posters" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    // A poster that fails to load (e.g. an arr-relative URL) falls back to the placeholder.
    fireEvent.error(within(card).getByRole("img"));
    expect(within(card).queryByRole("img")).not.toBeInTheDocument();
    expect(card).toHaveTextContent("The Wire");
  });

  it("marks an automation hold in the table and poster views", async () => {
    const held = [findingFixture({ automation_hold: true })];
    server.use(http.get("*/api/v1/findings", () => HttpResponse.json({ items: held, total: 1 })));
    const user = userEvent.setup();
    renderFindings();
    const table = await screen.findByRole("table");
    const heldRow = within(table).getByRole("row", { name: /Heat/ });
    expect(within(heldRow).getByText("Hold")).toHaveAttribute(
      "title",
      "Automation is paused for this item",
    );

    await user.click(screen.getByRole("button", { name: "Posters" }));
    const card = await screen.findByRole("article", { name: "Heat" });
    expect(within(card).getByText("Hold")).toHaveAttribute(
      "title",
      "Automation is paused for this item",
    );
  });

  it("bulk delete opens confirm with bytes", async () => {
    const previews: ActionsBody[] = [];
    const runs: ActionsBody[] = [];
    server.use(
      http.post("*/api/v1/actions/preview", async ({ request }) => {
        previews.push((await request.json()) as ActionsBody);
        return HttpResponse.json({
          items: 1,
          episodes: 0,
          bytes: 1288490189,
          rejected: [],
          per_finding: [{ finding_id: 1, episodes: 0 }],
        });
      }),
      http.post("*/api/v1/actions", async ({ request }) => {
        runs.push((await request.json()) as ActionsBody);
        return HttpResponse.json([logRow()]);
      }),
    );
    const user = userEvent.setup();
    renderFindings();
    await screen.findByRole("table");
    expect(screen.queryByRole("region", { name: "Bulk actions" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("checkbox", { name: "Select Heat" }));
    const bar = screen.getByRole("region", { name: "Bulk actions" });
    expect(bar).toHaveTextContent("1 selected");
    await user.click(within(bar).getByRole("button", { name: "Delete…" }));

    const dialog = await screen.findByRole("dialog");
    await within(dialog).findByText(/1\.2 GB/);
    expect(dialog).toHaveTextContent("1 item");
    expect(dialog).toHaveTextContent(/cannot be undone/i);
    expect(within(dialog).getByRole("radio", { name: "Delete files and unmonitor" })).toBeChecked();
    expect(previews[0].requests).toEqual([
      { finding_id: 1, action: "delete", scope: "item", delete_mode: "files", add_exclusion: false },
    ]);
    expect(dialog).not.toHaveTextContent("cannot be restored automatically");

    await user.click(within(dialog).getByRole("radio", { name: "Remove from Sonarr/Radarr" }));
    expect(dialog).toHaveTextContent("cannot be restored automatically");
    await user.click(within(dialog).getByRole("checkbox", { name: "Also add exclusion" }));
    await waitFor(() => expect(previews.at(-1)!.requests[0].add_exclusion).toBe(true));
    await within(dialog).findByText(/1\.2 GB/);
    await user.click(within(dialog).getByRole("button", { name: "Delete" }));

    await waitFor(() => expect(runs).toHaveLength(1));
    expect(runs[0]).toEqual({
      confirm: true,
      requests: [
        {
          finding_id: 1,
          action: "delete",
          scope: "item",
          delete_mode: "remove_item",
          add_exclusion: true,
        },
      ],
    });
    expect(await screen.findByText("Delete: 1 succeeded")).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Bulk actions" })).not.toBeInTheDocument();
    await waitFor(() => expect(findingsQueries.length).toBeGreaterThanOrEqual(2));
  });

  it("does not reuse a cached delete preview", async () => {
    let calls = 0;
    let release!: () => void;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    server.use(
      http.post("*/api/v1/actions/preview", async () => {
        calls += 1;
        if (calls > 1) await gate;
        return HttpResponse.json({
          items: 1,
          episodes: 0,
          bytes: 1,
          rejected: [],
          per_finding: [{ finding_id: 1, episodes: 0 }],
        });
      }),
    );
    const user = userEvent.setup();
    renderFindings();
    await screen.findByRole("table");
    await user.click(screen.getByRole("checkbox", { name: "Select Heat" }));
    await user.click(screen.getByRole("button", { name: "Delete…" }));
    let dialog = await screen.findByRole("dialog");
    expect(await within(dialog).findByText(/Deletes 1 item/)).toBeInTheDocument();

    // Close and reopen for the same target: the old preview must not let Delete through.
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Delete…" }));
    dialog = await screen.findByRole("dialog");
    await waitFor(() => expect(calls).toBe(2));
    expect(within(dialog).getByRole("button", { name: "Delete" })).toBeDisabled();

    release();
    await waitFor(() =>
      expect(within(dialog).getByRole("button", { name: "Delete" })).toBeEnabled(),
    );
  });

  it("moves off a page that no longer exists", async () => {
    let total = 51;
    const runs: ActionsBody[] = [];
    server.use(
      http.get("*/api/v1/findings", ({ request }) => {
        findingsQueries.push(new URL(request.url).searchParams);
        return HttpResponse.json({ items: FINDINGS, total });
      }),
      http.post("*/api/v1/actions", async ({ request }) => {
        runs.push((await request.json()) as ActionsBody);
        return HttpResponse.json([logRow()]);
      }),
    );
    const user = userEvent.setup();
    renderFindings("/findings?page=2");
    await screen.findByRole("table");
    await waitFor(() => expect(findingsQueries.at(-1)!.get("page")).toBe("2"));

    total = 2; // the action below shrinks the result to a single page
    await user.click(screen.getByRole("checkbox", { name: "Select Heat" }));
    const bar = screen.getByRole("region", { name: "Bulk actions" });
    await user.click(within(bar).getByRole("button", { name: "Unmonitor" }));

    await waitFor(() => expect(runs).toHaveLength(1));
    await waitFor(() => expect(screen.getByTestId("location")).not.toHaveTextContent("page=2"));
    // The refetch for the new URL is recorded a moment after the URL changes.
    await waitFor(() => expect(findingsQueries.at(-1)!.get("page")).toBeNull());
    expect(screen.getByText("1–2 of 2")).toBeInTheDocument();
  });

  it("bulk delete sends only what the preview accepts", async () => {
    const runs: ActionsBody[] = [];
    server.use(
      http.post("*/api/v1/actions/preview", () =>
        HttpResponse.json({
          items: 1,
          episodes: 0,
          bytes: 1288490189,
          rejected: [{ index: 1, finding_id: 2, reason: "Instance no longer configured" }],
          per_finding: [{ finding_id: 1, episodes: 0 }],
        }),
      ),
      http.post("*/api/v1/actions", async ({ request }) => {
        runs.push((await request.json()) as ActionsBody);
        return HttpResponse.json([logRow()]);
      }),
    );
    const user = userEvent.setup();
    renderFindings();
    await screen.findByRole("table");
    await user.click(screen.getByRole("checkbox", { name: "Select Heat" }));
    await user.click(screen.getByRole("checkbox", { name: "Select The Wire" }));
    await user.click(screen.getByRole("button", { name: "Delete…" }));
    const dialog = await screen.findByRole("dialog");
    const deleting = await within(dialog).findByRole("list", { name: "Will be deleted" });
    expect(within(deleting).getAllByRole("listitem").map((li) => li.textContent)).toEqual([
      "Heat",
    ]);
    expect(dialog).toHaveTextContent("The Wire cannot be deleted: Instance no longer configured");
    await user.click(within(dialog).getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(runs).toHaveLength(1));
    expect(runs[0].requests.map((r) => r.finding_id)).toEqual([1]);
  });

  it("filters write to URL", async () => {
    const user = userEvent.setup();
    renderFindings("/findings?lifecycle=lost");
    await screen.findByRole("table");
    expect(findingsQueries[0].get("lifecycle")).toBe("lost");
    expect(screen.getByRole("combobox", { name: "Lifecycle" })).toHaveValue("lost");

    await user.selectOptions(screen.getByRole("combobox", { name: "Type" }), "on_disk");
    expect(screen.getByTestId("location")).toHaveTextContent(
      "/findings?lifecycle=lost&type=on_disk",
    );
    await waitFor(() => expect(findingsQueries.at(-1)!.get("type")).toBe("on_disk"));
    expect(findingsQueries.at(-1)!.get("lifecycle")).toBe("lost");

    await user.selectOptions(screen.getByRole("combobox", { name: "Server" }), "m-b");
    await waitFor(() => expect(findingsQueries.at(-1)!.get("server")).toBe("m-b"));

    // Sortable header: Size toggles asc then desc.
    await user.click(screen.getByRole("button", { name: /Size/ }));
    expect(screen.getByTestId("location")).toHaveTextContent("sort=size");
    await user.click(screen.getByRole("button", { name: /Size/ }));
    expect(screen.getByTestId("location")).toHaveTextContent("sort=size&order=desc");
    await waitFor(() => expect(findingsQueries.at(-1)!.get("order")).toBe("desc"));
  });

  it("shows a 422 detail from an action", async () => {
    server.use(
      http.post("*/api/v1/actions", () =>
        HttpResponse.json(
          { detail: [{ msg: "Value error, remove_item is only valid with scope 'item'" }] },
          { status: 422 },
        ),
      ),
    );
    const user = userEvent.setup();
    renderFindings();
    await screen.findByRole("table");
    await user.click(screen.getByRole("checkbox", { name: "Select Heat" }));
    await user.click(screen.getByRole("button", { name: "Unmonitor" }));
    expect(
      await screen.findByText(
        "Unmonitor failed: Value error, remove_item is only valid with scope 'item'",
      ),
    ).toBeInTheDocument();
    // Selection is kept so the user can retry.
    expect(screen.getByRole("region", { name: "Bulk actions" })).toHaveTextContent("1 selected");
  });
});
