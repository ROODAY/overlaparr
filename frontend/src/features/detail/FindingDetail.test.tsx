import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { beforeEach, describe, expect, it } from "vitest";
import { Route, Routes } from "react-router-dom";
import type { FindingDetail as Detail } from "../../api/types";
import { FindingDetail } from "../../pages/FindingDetail";
import { activityFixture, movieDetailFixture, seriesDetailFixture } from "../../test/fixtures";
import { renderWithProviders } from "../../test/render";
import { server } from "../../test/server";

let detail: Detail;

function renderDetail(id = 1) {
  return renderWithProviders(
    <Routes>
      <Route path="/findings/:id" element={<FindingDetail />} />
    </Routes>,
    { route: `/findings/${id}` },
  );
}

beforeEach(() => {
  detail = movieDetailFixture();
  server.use(http.get("*/api/v1/findings/:id", () => HttpResponse.json(detail)));
});

describe("FindingDetail", () => {
  it("renders a movie's copies against yours", async () => {
    renderDetail();
    expect(await screen.findByRole("heading", { name: "Heat (1995)" })).toBeInTheDocument();
    const copies = screen.getByRole("table", { name: "Copies" });
    const yours = within(copies).getByRole("row", { name: /Yours/ });
    expect(yours).toHaveTextContent("1080p");
    expect(yours).toHaveTextContent("8.0 GB");
    const alpha = within(copies).getByRole("row", { name: /Alpha/ });
    for (const text of ["2160p", "HDR", "hevc", "Director's Cut", "20.0 GB"]) {
      expect(alpha).toHaveTextContent(text);
    }
    const bravo = within(copies).getByRole("row", { name: /Bravo/ });
    expect(bravo).toHaveAttribute("title", expect.stringMatching(/stale/));
    expect(bravo.className).toMatch(/opacity/);
    // Active finding without a hold: no banners.
    expect(screen.queryByText(/automation hold/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/last successful check/)).not.toBeInTheDocument();
    for (const name of ["Unmonitor", "Ignore…", "Delete…"]) {
      expect(screen.getByRole("button", { name })).toBeInTheDocument();
    }
  });

  it("clears the automation hold and flags stale copies of inactive findings", async () => {
    detail = movieDetailFixture({ automation_hold: true, lifecycle: "lost" });
    const holds: unknown[] = [];
    server.use(
      http.post("*/api/v1/findings/1/hold", async ({ request }) => {
        holds.push(await request.json());
        detail = { ...detail, automation_hold: false };
        return HttpResponse.json({ automation_hold: false });
      }),
    );
    const user = userEvent.setup();
    renderDetail();
    expect(await screen.findByText(/last successful check/)).toBeInTheDocument();
    // The hold can come from an undo or from a change made in Sonarr/Radarr.
    expect(screen.getByText(/changed outside an automatic action/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Clear automation hold" }));
    await waitFor(() => expect(holds).toEqual([{ hold: false }]));
    await waitFor(() =>
      expect(
        screen.queryByRole("button", { name: "Clear automation hold" }),
      ).not.toBeInTheDocument(),
    );
  });

  it("shows undo only for rows the API says can be undone", async () => {
    detail = movieDetailFixture({
      history: [
        activityFixture({ id: 7, action: "unmonitor" }),
        activityFixture({
          id: 8,
          action: "tag",
          undone_at: "2026-10-27T11:00:00Z",
          can_undo: false,
        }),
        // An error row the backend would refuse to undo, though flagged undoable.
        activityFixture({ id: 9, action: "exclude", result: "error", can_undo: false }),
        activityFixture({
          id: 10,
          action: "quality_profile",
          actor: "auto",
          undoable: false,
          can_undo: false,
        }),
      ],
    });
    const undos: string[] = [];
    server.use(
      http.post("*/api/v1/activity/:id/undo", ({ params }) => {
        undos.push(String(params.id));
        return HttpResponse.json({ detail: "Instance no longer configured" }, { status: 409 });
      }),
    );
    const user = userEvent.setup();
    renderDetail();
    const history = await screen.findByRole("list", { name: "History" });
    const rows = within(history).getAllByRole("listitem");
    expect(rows).toHaveLength(4);
    expect(within(rows[0]).getByRole("button", { name: "Undo" })).toBeInTheDocument();
    expect(within(rows[1]).queryByRole("button")).not.toBeInTheDocument();
    expect(rows[1]).toHaveTextContent("Undone");
    expect(within(rows[2]).queryByRole("button")).not.toBeInTheDocument();
    expect(rows[2]).toHaveTextContent("Cannot be undone");
    expect(rows[3]).toHaveTextContent("Set quality profile");
    expect(rows[3]).toHaveTextContent("Cannot be undone");

    await user.click(within(rows[0]).getByRole("button", { name: "Undo" }));
    expect(await screen.findByText("Undo failed: Instance no longer configured")).toBeInTheDocument();
    expect(undos).toEqual(["7"]);
  });

  it("says deleted files cannot be restored, even when the unmonitor can be undone", async () => {
    detail = movieDetailFixture({
      history: [
        activityFixture({
          id: 11,
          action: "delete",
          message: "1 file(s) deleted; undo restores monitoring only, deleted files are gone",
        }),
        activityFixture({ id: 12, action: "delete", undoable: false, can_undo: false }),
      ],
    });
    renderDetail();
    const history = await screen.findByRole("list", { name: "History" });
    const [undoable, plain] = within(history).getAllByRole("listitem");
    expect(within(undoable).getByRole("button", { name: "Undo unmonitor" })).toBeInTheDocument();
    expect(within(undoable).queryByRole("button", { name: "Undo" })).not.toBeInTheDocument();
    expect(undoable).toHaveTextContent("Files cannot be restored");
    expect(undoable).toHaveTextContent("undo restores monitoring only");
    expect(within(plain).queryByRole("button")).not.toBeInTheDocument();
    expect(plain).toHaveTextContent("Files cannot be restored");
    expect(plain).toHaveTextContent("Cannot be undone");
  });

  it("renders the season grid for a series", async () => {
    detail = seriesDetailFixture();
    renderDetail(5);
    expect(await screen.findByRole("heading", { name: "Severance (2022)" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^Season 1/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Delete series…" })).toBeInTheDocument();
  });
});
