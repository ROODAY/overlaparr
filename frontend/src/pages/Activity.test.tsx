import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { beforeEach, describe, expect, it } from "vitest";
import type { ActivityEntry } from "../api/types";
import { activityFixture, instanceFixture, ruleFixture } from "../test/fixtures";
import { renderWithProviders } from "../test/render";
import { server } from "../test/server";
import { Activity } from "./Activity";

let entries: ActivityEntry[];
let total: number;
let queries: URLSearchParams[];
let undos: string[];

beforeEach(() => {
  entries = [
    activityFixture(),
    activityFixture({
      id: 101,
      actor: "auto",
      action: "delete",
      item_title: "Alien",
      rule_id: "r-1",
      rule_name: "Unmonitor wanted",
      result: "error",
      message: "Radarr returned 500",
      undoable: false,
      can_undo: false,
    }),
    activityFixture({
      id: 102,
      item_title: "Ronin",
      undone_at: "2026-10-27T11:00:00Z",
      can_undo: false,
    }),
  ];
  total = 3;
  queries = [];
  undos = [];
  server.use(
    http.get("*/api/v1/activity", ({ request }) => {
      queries.push(new URL(request.url).searchParams);
      return HttpResponse.json({ items: entries, total });
    }),
    http.post("*/api/v1/activity/:id/undo", ({ params }) => {
      undos.push(String(params.id));
      const entry = entries.find((e) => String(e.id) === params.id)!;
      return HttpResponse.json({ ...entry, undone_at: "2026-10-27T12:00:00Z", can_undo: false });
    }),
    http.get("*/api/v1/instances", () =>
      HttpResponse.json([instanceFixture(), instanceFixture({ id: "i-2", name: "Radarr" })]),
    ),
    http.get("*/api/v1/rules", () => HttpResponse.json([ruleFixture()])),
  );
});

function row(name: RegExp) {
  return within(screen.getByRole("row", { name }));
}

const last = () => queries[queries.length - 1];

describe("Activity", () => {
  it("renders rows with result, rule and undo state", async () => {
    renderWithProviders(<Activity />, { route: "/activity" });
    expect(await screen.findByRole("heading", { name: "Activity" })).toBeInTheDocument();
    await screen.findByRole("row", { name: /Heat/ });

    const heat = row(/Heat/);
    expect(heat.getByText("UI")).toBeInTheDocument();
    expect(heat.getByText("OK")).toBeInTheDocument();
    expect(heat.getByRole("button", { name: "Undo unmonitor for Heat" })).toBeInTheDocument();

    const alien = row(/Alien/);
    expect(alien.getByText("Auto")).toBeInTheDocument();
    expect(alien.getByText("Unmonitor wanted")).toBeInTheDocument();
    expect(alien.getByText("Radarr returned 500")).toBeInTheDocument();
    expect(alien.getByText("Cannot be undone")).toBeInTheDocument();
    expect(alien.queryByRole("button", { name: /Undo/ })).not.toBeInTheDocument();

    expect(row(/Ronin/).getByText("Undone")).toBeInTheDocument();
    expect(screen.getByText(/Page 1 of 1/)).toBeInTheDocument();
  });

  it("undo posts to the undo endpoint and toasts", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Activity />, { route: "/activity" });
    await user.click(await screen.findByRole("button", { name: "Undo unmonitor for Heat" }));
    await waitFor(() => expect(undos).toEqual(["100"]));
    expect(await screen.findByText("Action undone")).toBeInTheDocument();
  });

  it("filters send query params", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Activity />, { route: "/activity" });
    await screen.findByRole("row", { name: /Heat/ });
    await screen.findByRole("option", { name: "Unmonitor wanted" });
    await screen.findByRole("option", { name: "Radarr" });

    await user.selectOptions(screen.getByLabelText("Actor"), "auto");
    await waitFor(() => expect(last().get("actor")).toBe("auto"));

    await user.selectOptions(screen.getByLabelText("Action"), "delete");
    await waitFor(() => expect(last().get("action")).toBe("delete"));

    await user.selectOptions(screen.getByLabelText("Instance"), "i-2");
    await waitFor(() => expect(last().get("instance")).toBe("i-2"));

    await user.selectOptions(screen.getByLabelText("Rule"), "r-1");
    await waitFor(() => expect(last().get("rule")).toBe("r-1"));

    expect(last().get("actor")).toBe("auto");
    expect(last().get("page_size")).toBe("50");
  });

  it("changing a filter resets the page to 1", async () => {
    total = 120;
    const user = userEvent.setup();
    renderWithProviders(<Activity />, { route: "/activity" });
    expect(await screen.findByText(/Page 1 of 3/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Next" }));
    await waitFor(() => expect(last().get("page")).toBe("2"));
    expect(await screen.findByText(/Page 2 of 3/)).toBeInTheDocument();

    await user.selectOptions(screen.getByLabelText("Actor"), "auto");
    await waitFor(() => expect(last().get("actor")).toBe("auto"));
    expect(last().get("page")).toBe("1");
    expect(await screen.findByText(/Page 1 of 3/)).toBeInTheDocument();
  });

  it("date filters send local day bounds", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Activity />, { route: "/activity" });
    await screen.findByRole("row", { name: /Heat/ });

    await user.type(screen.getByLabelText("From"), "2026-10-01");
    await waitFor(() =>
      expect(last().get("since")).toBe(new Date(2026, 9, 1, 0, 0, 0, 0).toISOString()),
    );
    await user.type(screen.getByLabelText("To"), "2026-10-02");
    await waitFor(() =>
      expect(last().get("until")).toBe(new Date(2026, 9, 2, 23, 59, 59, 999).toISOString()),
    );
  });

  it("pager Next requests page 2", async () => {
    total = 120;
    const user = userEvent.setup();
    renderWithProviders(<Activity />, { route: "/activity" });
    expect(await screen.findByText(/Page 1 of 3/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Next" }));
    await waitFor(() => expect(last().get("page")).toBe("2"));
    expect(await screen.findByText(/Page 2 of 3/)).toBeInTheDocument();
  });

  it("shows the empty state", async () => {
    entries = [];
    total = 0;
    renderWithProviders(<Activity />, { route: "/activity" });
    expect(await screen.findByText("No activity yet")).toBeInTheDocument();
  });

  it("shows an error with Retry", async () => {
    server.use(
      http.get("*/api/v1/activity", () =>
        HttpResponse.json({ detail: "Bad query" }, { status: 400 }),
      ),
    );
    renderWithProviders(<Activity />, { route: "/activity" });
    expect(await screen.findByRole("alert")).toHaveTextContent("Bad query");
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });
});
