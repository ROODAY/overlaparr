import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { beforeEach, describe, expect, it } from "vitest";
import { Route, Routes, useLocation } from "react-router-dom";
import type { PreviewIn, RuleIn } from "../api/types";
import { dashboardFixture, instanceFixture, ruleFixture } from "../test/fixtures";
import { renderWithProviders } from "../test/render";
import { server } from "../test/server";
import { RuleEditor } from "./RuleEditor";

const DELETE_WARNING =
  "Scheduled scans will permanently delete files from disk for every matching item.";

let previews: PreviewIn[] = [];
let created: RuleIn[] = [];
let updated: RuleIn[] = [];

function LocationProbe() {
  const location = useLocation();
  return <output data-testid="location">{location.pathname}</output>;
}

function renderEditor(route = "/automation/new") {
  return renderWithProviders(
    <>
      <Routes>
        <Route path="/automation/:id" element={<RuleEditor />} />
        <Route path="/automation" element={<p>Rules list</p>} />
      </Routes>
      <LocationProbe />
    </>,
    { route },
  );
}

function group(name: string) {
  return within(screen.getByRole("group", { name }));
}

function pressed(name: string) {
  return group(name)
    .getAllByRole("button")
    .filter((b) => b.getAttribute("aria-pressed") === "true")
    .map((b) => b.textContent);
}

beforeEach(() => {
  previews = [];
  created = [];
  updated = [];
  server.use(
    http.get("*/api/v1/dashboard", () => HttpResponse.json(dashboardFixture())),
    http.get("*/api/v1/instances", () =>
      HttpResponse.json([
        instanceFixture(),
        instanceFixture({ id: "i-3", name: "Radarr", type: "radarr" }),
      ]),
    ),
    http.get("*/api/v1/rules", () => HttpResponse.json([ruleFixture()])),
    http.post("*/api/v1/rules/preview", async ({ request }) => {
      const body = (await request.json()) as PreviewIn;
      previews.push(body);
      const worse = body.rule.quality?.includes("worse");
      const items = worse
        ? [1, 2, 3, 4, 5].map((n) => ({ finding_id: n, title: `Title ${n}`, episodes: 0 }))
        : [
            { finding_id: 1, title: "Heat", episodes: 0 },
            { finding_id: 2, title: "The Wire", episodes: 4 },
          ];
      return HttpResponse.json({ count: items.length, items });
    }),
    http.get("*/api/v1/rules/warning", ({ request }) => {
      const action = new URL(request.url).searchParams.get("action");
      return HttpResponse.json({
        text:
          action === "delete"
            ? `${DELETE_WARNING} Overlaparr cannot undo this.`
            : "Scheduled scans will unmonitor matching items in Sonarr/Radarr.",
      });
    }),
    http.post("*/api/v1/rules", async ({ request }) => {
      const body = (await request.json()) as RuleIn;
      created.push(body);
      return HttpResponse.json({ ...ruleFixture(), ...body, id: "r-new" }, { status: 201 });
    }),
    http.put("*/api/v1/rules/:id", async ({ request }) => {
      const body = (await request.json()) as RuleIn;
      updated.push(body);
      return HttpResponse.json({ ...ruleFixture(), ...body });
    }),
  );
});

describe("RuleEditor", () => {
  it("new rule shows defaults and is disabled", async () => {
    renderEditor();
    expect(await screen.findByRole("heading", { name: "New rule" })).toBeInTheDocument();
    expect(screen.getByRole("switch", { name: "Enabled" })).toHaveAttribute(
      "aria-checked",
      "false",
    );
    expect(screen.getByLabelText("Name")).toHaveValue("");
    expect(pressed("Action")).toEqual(["Unmonitor"]);
    expect(screen.queryByRole("group", { name: "Delete mode" })).not.toBeInTheDocument();
    expect(pressed("Finding type")).toEqual(["Wanted elsewhere"]);
    expect(await group("Instances").findByRole("button", { name: "Radarr" })).toBeInTheDocument();
    expect(pressed("Instances")).toEqual(["All instances"]);
    expect(await group("Servers").findByRole("button", { name: "Alpha" })).toBeInTheDocument();
    expect(pressed("Servers")).toEqual(["Any server"]);
    expect(screen.getByLabelText("Minimum servers")).toHaveValue(1);
    expect(pressed("Coverage (TV)")).toEqual(["Full only"]);
    expect(pressed("Their quality vs yours")).toEqual(["Better", "Same"]);
    expect(pressed("Edition (movies only)")).toEqual(["Same"]);
    expect(pressed("Episode matching (TV)")).toEqual(["By ID only"]);
    expect(screen.getByLabelText("Wait (consecutive scans)")).toHaveValue(2);
    // "All instances" includes Sonarr, so the editor explains edition is movie-only.
    expect(screen.getByText(/TV findings match regardless of edition/)).toBeInTheDocument();

    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Name"), "Safe rule");
    await user.click(screen.getByRole("button", { name: "Save rule" }));
    await waitFor(() => expect(created).toHaveLength(1));
    expect(created[0]).toMatchObject({ name: "Safe rule", enabled: false, delete_confirmed: false });
    expect(created[0]).not.toHaveProperty("dry_run_next");
    // Saving a disabled rule needs no warning.
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId("location")).toHaveTextContent(/^\/automation$/));
  });

  it("enabling delete requires typing DELETE", async () => {
    const user = userEvent.setup();
    renderEditor();
    await screen.findByRole("heading", { name: "New rule" });
    await user.type(screen.getByLabelText("Name"), "Free space");
    await user.click(group("Action").getByRole("button", { name: "Delete" }));
    expect(pressed("Delete mode")).toEqual(["Delete files"]);
    expect(screen.getByText(/series are never removed/)).toBeInTheDocument();
    await user.click(screen.getByRole("switch", { name: "Enabled" }));
    await user.click(screen.getByRole("button", { name: "Save rule" }));

    const dialog = await screen.findByRole("dialog");
    expect(await within(dialog).findByText(new RegExp(DELETE_WARNING))).toBeInTheDocument();
    const confirm = within(dialog).getByRole("button", { name: "Enable and save" });
    expect(confirm).toBeDisabled();
    const input = within(dialog).getByLabelText(/to confirm/);
    await user.type(input, "delete");
    expect(confirm).toBeDisabled();
    await user.clear(input);
    await user.type(input, "DELETE");
    expect(created).toHaveLength(0);
    await user.click(confirm);

    await waitFor(() => expect(created).toHaveLength(1));
    expect(created[0]).toMatchObject({
      name: "Free space",
      action: "delete",
      delete_mode: "files",
      enabled: true,
      delete_confirmed: true,
    });
    expect(created[0]).not.toHaveProperty("dry_run_next");
  });

  it("warns before enabling a non-delete rule without asking for DELETE", async () => {
    const user = userEvent.setup();
    renderEditor("/automation/r-1");
    expect(await screen.findByRole("heading", { name: "Edit rule" })).toBeInTheDocument();
    expect(screen.getByLabelText("Name")).toHaveValue("Unmonitor wanted");
    await user.click(screen.getByRole("switch", { name: "Enabled" }));
    await user.click(screen.getByRole("button", { name: "Save rule" }));
    const dialog = await screen.findByRole("dialog");
    expect(await within(dialog).findByText(/will unmonitor matching items/)).toBeInTheDocument();
    expect(within(dialog).queryByLabelText(/to confirm/)).not.toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Enable and save" }));
    await waitFor(() => expect(updated).toHaveLength(1));
    expect(updated[0]).toMatchObject({ enabled: true, delete_confirmed: false });
    expect(updated[0]).not.toHaveProperty("last_run");
  });

  it("preview count updates on change", async () => {
    const user = userEvent.setup();
    renderEditor();
    expect(await screen.findByText("Would unmonitor 2 items now")).toBeInTheDocument();
    await user.click(screen.getByText("Show list"));
    expect(screen.getByText("Heat")).toBeInTheDocument();
    expect(screen.getByText("The Wire (4 episodes)")).toBeInTheDocument();

    const before = previews.length;
    await user.click(group("Their quality vs yours").getByRole("button", { name: "Worse" }));
    expect(
      await screen.findByText("Would unmonitor 5 items now", {}, { timeout: 2000 }),
    ).toBeInTheDocument();
    expect(previews.length).toBe(before + 1);
    expect(previews.at(-1)!.rule.quality).toEqual(["better", "same", "worse"]);
  });

  it("does not re-preview for the name/enabled and keeps the count's own verb", async () => {
    const user = userEvent.setup();
    renderEditor();
    expect(await screen.findByText("Would unmonitor 2 items now")).toBeInTheDocument();

    const before = previews.length;
    await user.type(screen.getByLabelText("Name"), "Renamed");
    await user.click(screen.getByRole("switch", { name: "Enabled" }));
    // Neither field takes part in matching, so no request (not even after the debounce).
    await new Promise((resolve) => setTimeout(resolve, 600));
    expect(previews.length).toBe(before);

    // The count for the old conditions keeps its own verb while the new one loads.
    await user.click(group("Action").getByRole("button", { name: "Delete" }));
    expect(screen.getByText("Would unmonitor 2 items now")).toBeInTheDocument();
    expect(
      await screen.findByText("Would delete files of 2 items now", {}, { timeout: 2000 }),
    ).toBeInTheDocument();
    expect(previews.at(-1)!.rule.name).toBe("Preview");
  });

  it("shows an error when the preview fails", async () => {
    server.use(
      http.post("*/api/v1/rules/preview", () =>
        HttpResponse.json({ detail: "Could not read findings" }, { status: 422 }),
      ),
    );
    renderEditor();
    expect(
      await screen.findByText("Preview failed: Could not read findings", {}, { timeout: 3000 }),
    ).toBeInTheDocument();
  });

  it("says when instances could not be loaded and retries", async () => {
    let attempts = 0;
    server.use(
      http.get("*/api/v1/instances", () => {
        attempts += 1;
        return attempts === 1
          ? HttpResponse.json({ detail: "Plex is not connected" }, { status: 409 })
          : HttpResponse.json([instanceFixture()]);
      }),
    );
    const user = userEvent.setup();
    renderEditor();
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Could not load instances");
    expect(alert).toHaveTextContent("Plex is not connected");
    await user.click(within(alert).getByRole("button", { name: "Retry" }));
    expect(await group("Instances").findByRole("button", { name: "Sonarr" })).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("names every source that could not be loaded and retries them", async () => {
    let instanceAttempts = 0;
    let dashboardAttempts = 0;
    server.use(
      http.get("*/api/v1/instances", () => {
        instanceAttempts += 1;
        return instanceAttempts === 1
          ? HttpResponse.json({ detail: "Plex is not connected" }, { status: 409 })
          : HttpResponse.json([instanceFixture()]);
      }),
      http.get("*/api/v1/dashboard", () => {
        dashboardAttempts += 1;
        return dashboardAttempts === 1
          ? HttpResponse.json({ detail: "Could not read servers" }, { status: 409 })
          : HttpResponse.json(dashboardFixture());
      }),
    );
    const user = userEvent.setup();
    renderEditor();
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Could not load instances and servers");
    expect(alert).toHaveTextContent("Plex is not connected; Could not read servers");
    await user.click(within(alert).getByRole("button", { name: "Retry" }));
    expect(await group("Instances").findByRole("button", { name: "Sonarr" })).toBeInTheDocument();
    expect(await group("Servers").findByRole("button", { name: "Alpha" })).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("keeps the form and unsaved edits when the rules refetch fails", async () => {
    let calls = 0;
    server.use(
      http.get("*/api/v1/rules", () => {
        calls += 1;
        return calls === 1
          ? HttpResponse.json([ruleFixture()])
          : HttpResponse.json({ detail: "Could not read rules" }, { status: 400 });
      }),
    );
    const user = userEvent.setup();
    const { queryClient } = renderEditor("/automation/r-1");
    const name = await screen.findByLabelText("Name");
    await user.type(name, " (edited)");

    await queryClient.refetchQueries({ queryKey: ["rules"] });
    expect(
      await screen.findByText("Could not refresh the rules: Could not read rules"),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("Name")).toHaveValue("Unmonitor wanted (edited)");
    expect(screen.getByRole("button", { name: "Save rule" })).toBeInTheDocument();
  });

  it("deletes the rule after confirmation", async () => {
    const deletes: string[] = [];
    server.use(
      http.delete("*/api/v1/rules/:id", ({ params }) => {
        deletes.push(String(params.id));
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const user = userEvent.setup();
    renderEditor("/automation/r-1");
    await screen.findByRole("heading", { name: "Edit rule" });
    // The dialog names the saved rule even while the draft name is blank.
    await user.clear(screen.getByLabelText("Name"));

    await user.click(screen.getByRole("button", { name: "Delete rule" }));
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveAccessibleName("Delete Unmonitor wanted?");
    expect(dialog).toHaveTextContent(
      "This removes the rule from config.yaml. Past activity stays in the log.",
    );
    expect(deletes).toEqual([]);

    await user.click(within(dialog).getByRole("button", { name: "Delete rule" }));
    await waitFor(() => expect(deletes).toEqual(["r-1"]));
    expect(await screen.findByText("Rule deleted")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId("location")).toHaveTextContent(/^\/automation$/));
  });

  it("warns that a whole-item delete rule removes the files with it", async () => {
    const user = userEvent.setup();
    renderEditor();
    await screen.findByRole("heading", { name: "New rule" });
    await user.type(screen.getByLabelText("Name"), "Whole items");
    await user.click(group("Action").getByRole("button", { name: "Delete" }));
    await user.click(group("Delete mode").getByRole("button", { name: "Remove from Radarr" }));
    await user.click(screen.getByRole("switch", { name: "Enabled" }));
    await user.click(screen.getByRole("button", { name: "Save rule" }));

    const dialog = await screen.findByRole("dialog");
    expect(
      await within(dialog).findByText(
        /This removes whole movies\/series from Radarr\/Sonarr with their files\./,
      ),
    ).toBeInTheDocument();
    expect(within(dialog).getByText(/Loss handling cannot restore them/)).toBeInTheDocument();
  });

  it("shows the backend rejection", async () => {
    server.use(
      http.post("*/api/v1/rules", () =>
        HttpResponse.json(
          { detail: [{ msg: "Input should be greater than or equal to 1" }] },
          { status: 422 },
        ),
      ),
    );
    const user = userEvent.setup();
    renderEditor();
    await screen.findByRole("heading", { name: "New rule" });
    await user.type(screen.getByLabelText("Name"), "x");
    await user.click(screen.getByRole("button", { name: "Save rule" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Input should be greater than or equal to 1",
    );
  });
});
