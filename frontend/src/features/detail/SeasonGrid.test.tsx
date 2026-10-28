import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import type { ActionsBody } from "../../api/types";
import { seriesDetailFixture } from "../../test/fixtures";
import { renderWithProviders } from "../../test/render";
import { server } from "../../test/server";
import { SeasonGrid } from "./SeasonGrid";

function renderGrid() {
  return renderWithProviders(<SeasonGrid detail={seriesDetailFixture()} />);
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
