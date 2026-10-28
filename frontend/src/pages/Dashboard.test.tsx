import { screen, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { fakeEventSourceFactory } from "../test/fakeEventSource";
import { dashboardFixture } from "../test/fixtures";
import { renderWithProviders } from "../test/render";
import { server } from "../test/server";
import { Dashboard } from "./Dashboard";

const OPTIONS = { createEventSource: fakeEventSourceFactory, pollMs: 20 };

function renderDashboard(data = dashboardFixture()) {
  server.use(http.get("*/api/v1/dashboard", () => HttpResponse.json(data)));
  return renderWithProviders(<Dashboard scanOptions={OPTIONS} />);
}

function countFor(region: HTMLElement, label: string) {
  return within(region).getByText(label).closest("div")!;
}

describe("Dashboard", () => {
  it("shows finding counts, reclaimable space and lost findings", async () => {
    renderDashboard();
    const counts = await screen.findByRole("region", { name: "Findings" });
    expect(countFor(counts, "Wanted elsewhere")).toHaveTextContent("4");
    expect(countFor(counts, "On disk")).toHaveTextContent("7");
    expect(countFor(counts, "Partial")).toHaveTextContent("2");
    expect(countFor(counts, "New")).toHaveTextContent("5");
    expect(screen.getByRole("region", { name: "Reclaimable space" })).toHaveTextContent("1.2 GB");
    const lost = screen.getByRole("link", { name: /3 lost findings/ });
    expect(lost).toHaveAttribute("href", "/findings?lifecycle=lost");
    expect(screen.getByRole("button", { name: "Check now" })).toBeInTheDocument();
  });

  it("shows scan timing, server health and instance status", async () => {
    renderDashboard(
      dashboardFixture({
        last_scan: {
          id: 12,
          started_at: "2026-10-26T10:00:00Z",
          finished_at: "2026-10-26T10:01:00Z",
          status: "failed",
          trigger: "manual",
          error: "Plex unreachable",
          summary: {},
        },
        servers: [
          ...dashboardFixture().servers,
          {
            id: "m-c",
            label: "Charlie",
            reachability: "partial",
            last_indexed_at: "2026-10-26T09:00:00Z",
          },
        ],
      }),
    );
    const scans = await screen.findByRole("region", { name: "Scans" });
    expect(scans).toHaveTextContent("failed");
    expect(scans).toHaveTextContent("Plex unreachable");
    expect(scans).toHaveTextContent("Next scan");

    const servers = screen.getByRole("region", { name: "Servers" });
    const bravo = within(servers).getByText("Bravo").closest("li")!;
    expect(bravo).toHaveTextContent(/stale — last seen/);
    expect(within(servers).getByText("Alpha").closest("li")).toHaveTextContent("online");
    expect(within(servers).getByText("Charlie").closest("li")).toHaveTextContent(
      /partial — last indexed/,
    );
    expect(servers).toHaveTextContent(/never treated as content removed/);

    const instances = screen.getByRole("region", { name: "Instances" });
    expect(within(instances).getByText("Sonarr").closest("li")).toHaveTextContent("ok");
    expect(within(instances).getByText("Radarr 4K").closest("li")).toHaveTextContent("failed");
    expect(within(instances).getByText("Radarr").closest("li")).toHaveTextContent(
      "not checked yet",
    );
  });

  it("omits the next scan when none is scheduled and says when nothing has run", async () => {
    renderDashboard(dashboardFixture({ next_scan: null, last_scan: null, lost: 0 }));
    const scans = await screen.findByRole("region", { name: "Scans" });
    expect(scans).toHaveTextContent("No scan yet");
    expect(scans).not.toHaveTextContent("Next scan");
    expect(screen.queryByRole("link", { name: /lost/ })).not.toBeInTheDocument();
  });
});
