import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { beforeEach, describe, expect, it } from "vitest";
import type { RuleIn, RuleWithRun } from "../api/types";
import { ruleFixture } from "../test/fixtures";
import { renderWithProviders } from "../test/render";
import { server } from "../test/server";
import { Automation } from "./Automation";

let rules: RuleWithRun[] = [];
let patches: { id: string; body: RuleIn }[] = [];
let reorders: string[][] = [];
let dryRuns: string[] = [];

function row(name: string) {
  return within(screen.getByRole("row", { name: new RegExp(name) }));
}

beforeEach(() => {
  rules = [
    ruleFixture(),
    ruleFixture({
      id: "r-2",
      name: "Delete on disk",
      enabled: true,
      action: "delete",
      delete_confirmed: true,
      finding_types: ["on_disk"],
      last_run: "2026-10-26T10:05:00Z",
      last_affected: 7,
    }),
    ruleFixture({ id: "r-3", name: "Purge", action: "delete" }),
  ];
  patches = [];
  reorders = [];
  dryRuns = [];
  server.use(
    http.get("*/api/v1/rules", () => HttpResponse.json(rules)),
    http.get("*/api/v1/rules/warning", ({ request }) => {
      const action = new URL(request.url).searchParams.get("action");
      return HttpResponse.json({ text: `Warning for ${action}.` });
    }),
    http.patch("*/api/v1/rules/:id", async ({ params, request }) => {
      const body = (await request.json()) as RuleIn;
      const id = String(params.id);
      patches.push({ id, body });
      rules = rules.map((r) => (r.id === id ? { ...r, ...body } : r));
      return HttpResponse.json(rules.find((r) => r.id === id));
    }),
    http.post("*/api/v1/rules/reorder", async ({ request }) => {
      const { ids } = (await request.json()) as { ids: string[] };
      reorders.push(ids);
      rules = ids.map((id) => rules.find((r) => r.id === id)!);
      // The real endpoint returns plain rules: last_run/last_affected are list-only.
      return HttpResponse.json(rules.map(({ last_run: _r, last_affected: _a, ...rule }) => rule));
    }),
    http.post("*/api/v1/rules/:id/dry-run", ({ params }) => {
      dryRuns.push(String(params.id));
      rules = rules.map((r) => (r.id === params.id ? { ...r, dry_run_next: true } : r));
      return HttpResponse.json(rules.find((r) => r.id === params.id));
    }),
  );
});

describe("Automation", () => {
  it("lists rules with last run and items affected", async () => {
    renderWithProviders(<Automation />, { route: "/automation" });
    expect(await screen.findByRole("link", { name: "Unmonitor wanted" })).toHaveAttribute(
      "href",
      "/automation/r-1",
    );
    expect(screen.getByRole("link", { name: "New rule" })).toHaveAttribute(
      "href",
      "/automation/new",
    );
    expect(row("Unmonitor wanted").getByText("Never")).toBeInTheDocument();
    expect(row("Delete on disk").getByText("7")).toBeInTheDocument();
  });

  it("enables with a warning via PATCH and disables without one", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Automation />, { route: "/automation" });
    await screen.findByRole("link", { name: "Unmonitor wanted" });

    await user.click(row("Unmonitor wanted").getByRole("switch", { name: "Enable Unmonitor wanted" }));
    const dialog = await screen.findByRole("dialog");
    expect(await within(dialog).findByText("Warning for unmonitor.")).toBeInTheDocument();
    expect(patches).toHaveLength(0);
    await user.click(within(dialog).getByRole("button", { name: "Enable" }));
    await waitFor(() => expect(patches).toEqual([{ id: "r-1", body: { enabled: true } }]));
    await waitFor(() =>
      expect(row("Unmonitor wanted").getByRole("switch", { name: "Enable Unmonitor wanted" })).toHaveAttribute(
        "aria-checked",
        "true",
      ),
    );

    await user.click(row("Delete on disk").getByRole("switch", { name: "Enable Delete on disk" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await waitFor(() => expect(patches.at(-1)).toEqual({ id: "r-2", body: { enabled: false } }));
  });

  it("enabling a delete rule from the list requires typing DELETE", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Automation />, { route: "/automation" });
    await screen.findByRole("link", { name: "Purge" });
    await user.click(row("Purge").getByRole("switch", { name: "Enable Purge" }));
    const dialog = await screen.findByRole("dialog");
    expect(await within(dialog).findByText("Warning for delete.")).toBeInTheDocument();
    const confirm = within(dialog).getByRole("button", { name: "Enable" });
    expect(confirm).toBeDisabled();
    await user.type(within(dialog).getByLabelText(/to confirm/), "DELETE");
    await user.click(confirm);
    await waitFor(() =>
      expect(patches).toEqual([{ id: "r-3", body: { enabled: true, delete_confirmed: true } }]),
    );
  });

  it("warns that a whole-item delete rule removes the files with it", async () => {
    rules = [
      ruleFixture({
        id: "r-4",
        name: "Remove whole items",
        action: "delete",
        delete_mode: "remove_item",
      }),
      ruleFixture({ id: "r-5", name: "Delete files", action: "delete", delete_mode: "files" }),
    ];
    const user = userEvent.setup();
    renderWithProviders(<Automation />, { route: "/automation" });
    await screen.findByRole("link", { name: "Remove whole items" });

    await user.click(
      row("Remove whole items").getByRole("switch", { name: "Enable Remove whole items" }),
    );
    let dialog = await screen.findByRole("dialog");
    expect(
      await within(dialog).findByText(
        /This removes whole movies\/series from Radarr\/Sonarr with their files\./,
      ),
    ).toBeInTheDocument();
    expect(within(dialog).getByText(/Loss handling cannot restore them/)).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));

    // A files-mode delete rule does not get the whole-item warning.
    await user.click(row("Delete files").getByRole("switch", { name: "Enable Delete files" }));
    dialog = await screen.findByRole("dialog");
    expect(await within(dialog).findByText("Warning for delete.")).toBeInTheDocument();
    expect(within(dialog).queryByText(/Loss handling cannot restore them/)).not.toBeInTheDocument();
  });

  it("reorders with up/down buttons", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Automation />, { route: "/automation" });
    await screen.findByRole("link", { name: "Unmonitor wanted" });
    expect(row("Unmonitor wanted").getByRole("button", { name: "Move up" })).toBeDisabled();
    expect(row("Purge").getByRole("button", { name: "Move down" })).toBeDisabled();
    await user.click(row("Unmonitor wanted").getByRole("button", { name: "Move down" }));
    await waitFor(() => expect(reorders).toEqual([["r-2", "r-1", "r-3"]]));
    await waitFor(() =>
      expect(screen.getAllByRole("link").map((l) => l.textContent)).toEqual([
        "New rule",
        "Delete on disk",
        "Unmonitor wanted",
        "Purge",
      ]),
    );
    // The reorder response carries no run fields; the cached ones must survive.
    expect(row("Delete on disk").getByText("7")).toBeInTheDocument();
    expect(row("Delete on disk").queryByText("Never")).not.toBeInTheDocument();
    expect(row("Delete on disk").getByText(/ago|yesterday|second|minute/)).toBeInTheDocument();
  });

  it("keeps the switch off and toasts when enabling fails", async () => {
    server.use(
      http.patch("*/api/v1/rules/:id", () =>
        HttpResponse.json({ detail: 'Delete rules require confirmation' }, { status: 422 }),
      ),
    );
    const user = userEvent.setup();
    renderWithProviders(<Automation />, { route: "/automation" });
    await screen.findByRole("link", { name: "Unmonitor wanted" });
    await user.click(row("Unmonitor wanted").getByRole("switch", { name: "Enable Unmonitor wanted" }));
    await user.click(
      within(await screen.findByRole("dialog")).getByRole("button", { name: "Enable" }),
    );
    expect(
      await screen.findByText("Could not update the rule: Delete rules require confirmation"),
    ).toBeInTheDocument();
    await waitFor(() =>
      expect(
        row("Unmonitor wanted").getByRole("switch", { name: "Enable Unmonitor wanted" }),
      ).toHaveAttribute("aria-checked", "false"),
    );
  });

  it("queues a dry run for the next scan", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Automation />, { route: "/automation" });
    await screen.findByRole("link", { name: "Purge" });
    await user.click(row("Purge").getByRole("button", { name: "Dry run" }));
    await waitFor(() => expect(dryRuns).toEqual(["r-3"]));
    expect(
      await screen.findByText(/next scan will report what this rule would do/i),
    ).toBeInTheDocument();
    await waitFor(() => expect(row("Purge").getByText("Dry run queued")).toBeInTheDocument());
  });
});
