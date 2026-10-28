import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import type { ActionsBody } from "../../api/types";
import { seriesDetailFixture } from "../../test/fixtures";
import { renderWithProviders } from "../../test/render";
import { server } from "../../test/server";
import { SeasonGrid } from "./SeasonGrid";

function renderGrid(overrides: Parameters<typeof seriesDetailFixture>[0] = {}) {
  return renderWithProviders(<SeasonGrid detail={seriesDetailFixture(overrides)} />);
}

const box = (title: string) => screen.getByRole("checkbox", { name: `Select ${title}` });
const season = (n: number) => screen.getByRole("button", { name: new RegExp(`^Season ${n} `) });
const bar = () => screen.getByRole("region", { name: "Actions" });

describe("SeasonGrid", () => {
  it("initial selection = covered episodes", async () => {
    const user = userEvent.setup();
    renderGrid();
    // Opens on the first season; its buttons summarise coverage and servers.
    expect(season(1)).toHaveAttribute("aria-pressed", "true");
    expect(season(1)).toHaveTextContent("2/2 covered · bob, carol");
    expect(season(2)).toHaveTextContent("1/2 covered · carol");
    expect(box("Good News About Hell")).toBeChecked();
    expect(box("Half Loop")).toBeChecked();
    await user.click(season(2));
    expect(box("Hello, Ms. Cobel")).toBeChecked();
    expect(box("Goodbye, Mrs. Selvig")).not.toBeChecked();
    expect(bar()).toHaveTextContent("3 selected");
    // Yours: resolution + size, or Wanted.
    expect(screen.getByRole("row", { name: /Goodbye, Mrs. Selvig/ })).toHaveTextContent("Wanted");
  });

  it("selection survives season switch", async () => {
    const user = userEvent.setup();
    renderGrid();
    await user.click(box("Half Loop"));
    await user.click(season(2));
    await user.click(box("Goodbye, Mrs. Selvig"));
    await user.click(season(1));
    expect(box("Good News About Hell")).toBeChecked();
    expect(box("Half Loop")).not.toBeChecked();
    expect(bar()).toHaveTextContent("3 selected");
    await user.click(screen.getByRole("button", { name: "None" }));
    expect(bar()).toHaveTextContent("0 selected");
    await user.click(screen.getByRole("button", { name: "Covered" }));
    expect(bar()).toHaveTextContent("3 selected");
  });

  it("server with no episodes in season has no column", async () => {
    const user = userEvent.setup();
    renderGrid();
    const headers = () => screen.getAllByRole("columnheader").map((h) => h.textContent);
    expect(headers()).toEqual(expect.arrayContaining(["bob", "carol"]));
    await user.click(season(2));
    expect(headers()).toContain("carol");
    expect(headers()).not.toContain("bob");
  });

  it("number match shows marker", () => {
    renderGrid();
    const halfLoop = screen.getByRole("row", { name: /Half Loop/ });
    const cell = within(halfLoop).getByText(/by number/);
    expect(cell).toHaveTextContent("720p · by number");
    expect(cell.className).toMatch(/amber/);
    expect(screen.getByRole("row", { name: /Good News About Hell/ })).not.toHaveTextContent(
      "by number",
    );
    // A stale copy is dimmed with a tooltip, not shown as gone.
    const stale = within(halfLoop).getByTitle(/stale/);
    expect(stale).toHaveTextContent("2160p HDR");
  });

  it("deletes the selected episode files without offering remove mode", async () => {
    const previews: ActionsBody[] = [];
    const runs: ActionsBody[] = [];
    server.use(
      http.post("*/api/v1/actions/preview", async ({ request }) => {
        previews.push((await request.json()) as ActionsBody);
        return HttpResponse.json({
          items: 1,
          episodes: 2,
          bytes: 4_724_464_025,
          rejected: [],
          per_finding: [{ finding_id: 5, episodes: 2 }],
        });
      }),
      http.post("*/api/v1/actions", async ({ request }) => {
        runs.push((await request.json()) as ActionsBody);
        return HttpResponse.json([]);
      }),
    );
    const user = userEvent.setup();
    renderGrid();
    expect(bar()).toHaveTextContent("3 selected · ≈4.4 GB"); // S2E1 is wanted: no bytes
    await user.click(season(2));
    await user.click(box("Hello, Ms. Cobel"));
    await user.click(within(bar()).getByRole("button", { name: "Delete selected files…" }));
    const dialog = await screen.findByRole("dialog");
    await within(dialog).findByText(/4\.4 GB/);
    expect(within(dialog).queryByRole("radio")).not.toBeInTheDocument();
    expect(dialog).toHaveTextContent("Severance (2 episodes)");
    await user.click(within(dialog).getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(runs).toHaveLength(1));
    const expected = {
      finding_id: 5,
      action: "delete",
      scope: "episodes",
      episode_ids: [101, 102],
      delete_mode: "files",
      add_exclusion: false,
    };
    expect(previews[0].requests).toEqual([expected]);
    expect(runs[0]).toEqual({ confirm: true, requests: [expected] });
  });

  it("has nothing to delete when the selected episodes have no files", async () => {
    server.use(
      http.post("*/api/v1/actions/preview", () =>
        HttpResponse.json({
          items: 1,
          episodes: 0,
          bytes: 0,
          rejected: [],
          per_finding: [{ finding_id: 5, episodes: 0 }],
        }),
      ),
    );
    const user = userEvent.setup();
    renderGrid();
    await user.click(screen.getByRole("button", { name: "None" }));
    await user.click(season(2));
    await user.click(box("Hello, Ms. Cobel"));
    await user.click(within(bar()).getByRole("button", { name: "Delete selected files…" }));
    const dialog = await screen.findByRole("dialog");
    expect(await within(dialog).findByText(/Nothing to delete/)).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Delete" })).toBeDisabled();
  });

  it("offers per-server ignore only once the finding is ignored", async () => {
    const user = userEvent.setup();
    renderGrid({ review_state: "ignored" });
    await user.click(screen.getByRole("button", { name: "Ignore…" }));
    const menu = screen.getByRole("group", { name: "Ignore" });
    expect(within(menu).queryByRole("button", { name: "Whole finding" })).not.toBeInTheDocument();
    expect(within(menu).getByRole("button", { name: "Only bob's copies" })).toBeInTheDocument();
  });

  it("unmonitors the selected episodes across seasons", async () => {
    const runs: ActionsBody[] = [];
    server.use(
      http.post("*/api/v1/actions", async ({ request }) => {
        runs.push((await request.json()) as ActionsBody);
        return HttpResponse.json([]);
      }),
    );
    const user = userEvent.setup();
    renderGrid();
    await user.click(within(bar()).getByRole("button", { name: "Unmonitor selected" }));
    await waitFor(() => expect(runs).toHaveLength(1));
    expect(runs[0]).toEqual({
      confirm: false,
      requests: [
        { finding_id: 5, action: "unmonitor", scope: "episodes", episode_ids: [101, 102, 201] },
      ],
    });
  });
});
