import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { beforeEach, describe, expect, it } from "vitest";
import type { PasswordChange, Settings as SettingsData, SettingsUpdate } from "../api/types";
import { formatDateTime } from "../lib/format";
import { renderWithProviders } from "../test/render";
import { server } from "../test/server";
import { Settings } from "./Settings";

function settingsFixture(overrides: Partial<SettingsData> = {}): SettingsData {
  return {
    plex: { token: null, home_server: null },
    servers: [],
    instances: [],
    scan: {
      cron: "0 0 * * *",
      server_timeout_seconds: 10,
      server_index_budget_seconds: 600,
      page_size: 500,
      stale_after_hours: 48,
    },
    tags: {
      enabled: false,
      dupe: "dupe-elsewhere",
      partial: "dupe-partial",
      ignore: "dupe-ignore",
      per_server_tags: false,
    },
    rules: [],
    notifications: { app_url: null, connections: [] },
    logging: { level: "INFO", max_bytes: 10485760, backup_count: 5 },
    action_log: { retention_days: null },
    auth: { local_bypass: false },
    config_error: null,
    ...overrides,
  };
}

let settings: SettingsData;
let puts: SettingsUpdate[];
let previews: string[];
let previewNext: string[];

beforeEach(() => {
  settings = settingsFixture();
  puts = [];
  previews = [];
  previewNext = ["2026-10-28T00:00:00Z", "2026-10-29T00:00:00Z"];
  server.use(
    http.get("*/api/v1/settings", () => HttpResponse.json(settings)),
    http.get("*/api/v1/settings/cron-preview", ({ request }) => {
      const cron = new URL(request.url).searchParams.get("cron") ?? "";
      previews.push(cron);
      if (cron === "bad") {
        return HttpResponse.json({ detail: "Invalid cron expression" }, { status: 422 });
      }
      return HttpResponse.json({ next: previewNext });
    }),
    http.put("*/api/v1/settings", async ({ request }) => {
      const body = (await request.json()) as SettingsUpdate;
      puts.push(body);
      settings = {
        ...settings,
        scan: { ...settings.scan, ...body.scan },
        tags: { ...settings.tags, ...body.tags },
        logging: { ...settings.logging, ...body.logging },
        action_log: { ...settings.action_log, ...body.action_log },
        auth: { ...settings.auth, ...body.auth },
        notifications: { ...settings.notifications, ...body.notifications },
      };
      return HttpResponse.json(settings);
    }),
  );
});

describe("Settings", () => {
  it("cron preview renders next runs", async () => {
    renderWithProviders(<Settings />, { route: "/settings" });
    expect(await screen.findByRole("heading", { name: "Settings" })).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "Next 5 runs" })).toBeInTheDocument();

    expect(
      await screen.findByText(formatDateTime(previewNext[0]), {}, { timeout: 3000 }),
    ).toBeInTheDocument();
    expect(screen.getByText(formatDateTime(previewNext[1]))).toBeInTheDocument();
  });

  it("choosing the every-6-hours preset updates the cron and previews it", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Settings />, { route: "/settings" });
    const select = await screen.findByLabelText("Scan schedule");
    expect(select).toHaveValue("0 0 * * *");

    await user.selectOptions(select, "0 */6 * * *");
    expect(select).toHaveValue("0 */6 * * *");
    await waitFor(() => expect(previews).toContain("0 */6 * * *"), { timeout: 3000 });

    // The preset wrote the expression into the cron field: Custom reveals it.
    await user.selectOptions(select, "custom");
    expect(screen.getByLabelText("Cron expression")).toHaveValue("0 */6 * * *");
  });

  it("shows the preview error for an invalid cron", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Settings />, { route: "/settings" });
    const select = await screen.findByLabelText("Scan schedule");
    await user.selectOptions(select, "custom");

    const cron = screen.getByLabelText("Cron expression");
    await user.clear(cron);
    await user.type(cron, "bad");

    expect(
      await screen.findByText("Preview failed: Invalid cron expression", {}, { timeout: 3000 }),
    ).toBeInTheDocument();
    expect(screen.queryByText(formatDateTime(previewNext[0]))).not.toBeInTheDocument();
  });

  it("saves the edited sections with one PUT and toasts", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Settings />, { route: "/settings" });
    await screen.findByLabelText("Enabled");

    await user.click(screen.getByLabelText("Enabled"));
    await user.click(screen.getByLabelText("Disable login for local addresses"));
    await user.click(screen.getByRole("button", { name: "Save settings" }));

    await waitFor(() => expect(puts).toHaveLength(1));
    expect(puts[0].tags).toMatchObject({ enabled: true });
    expect(puts[0].auth).toEqual({ local_bypass: true });
    // Numbers go out as numbers; a blank retention is null.
    expect(puts[0].scan).toMatchObject({ server_timeout_seconds: 10, stale_after_hours: 48 });
    expect(puts[0].logging).toMatchObject({ max_bytes: 10485760, backup_count: 5 });
    expect(puts[0].action_log).toEqual({ retention_days: null });
    expect(await screen.findByText("Settings saved")).toBeInTheDocument();
  });

  it("shows the local-bypass warning only when checked", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Settings />, { route: "/settings" });
    const toggle = await screen.findByLabelText("Disable login for local addresses");
    expect(screen.queryByText(/every request looks local/i)).not.toBeInTheDocument();

    await user.click(toggle);
    expect(screen.getByText(/every request looks local/i)).toBeInTheDocument();
  });

  it("links to the config.yaml download", async () => {
    renderWithProviders(<Settings />, { route: "/settings" });
    const link = await screen.findByRole("link", { name: "Download config.yaml" });
    expect(link).toHaveAttribute("href", "/api/v1/settings/config.yaml");
    expect(link).toHaveAttribute("download");
  });

  it("leaves the config error banner to the layout but says saving is blocked", async () => {
    settings = settingsFixture({ config_error: "scan.cron: invalid cron expression" });
    renderWithProviders(<Settings />, { route: "/settings" });
    expect(await screen.findByLabelText("Enabled")).toBeInTheDocument();
    // The layout shows the banner; Settings must not repeat it.
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByText(/Saving is blocked while config\.yaml is invalid/)).toBeInTheDocument();
  });

  it("keeps the form and unsaved edits when a background refetch fails", async () => {
    let calls = 0;
    server.use(
      http.get("*/api/v1/settings", () => {
        calls += 1;
        return calls === 1
          ? HttpResponse.json(settings)
          : HttpResponse.json({ detail: "Config store unavailable" }, { status: 400 });
      }),
    );
    const user = userEvent.setup();
    const { queryClient } = renderWithProviders(<Settings />, { route: "/settings" });
    await screen.findByLabelText("Enabled");
    await user.click(screen.getByLabelText("Enabled"));
    expect(screen.getByLabelText("Enabled")).toBeChecked();

    await queryClient.refetchQueries({ queryKey: ["settings"] });
    expect(
      await screen.findByText("Could not refresh settings: Config store unavailable"),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("Enabled")).toBeChecked();
    expect(screen.getByRole("button", { name: "Save settings" })).toBeInTheDocument();
  });

  it("does not save a blank numeric setting as zero", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Settings />, { route: "/settings" });
    const backups = await screen.findByLabelText("Backups");
    await user.clear(backups);

    expect(screen.getByText("Enter a number")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Save settings" }));
    expect(puts).toEqual([]);
  });

  it("changes the password and toasts", async () => {
    const changes: PasswordChange[] = [];
    server.use(
      http.post("*/api/v1/auth/password", async ({ request }) => {
        changes.push((await request.json()) as PasswordChange);
        return HttpResponse.json({ ok: true });
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<Settings />, { route: "/settings" });
    await user.type(await screen.findByLabelText("Current password"), "old-secret");
    await user.type(screen.getByLabelText("New password"), "new-secret");
    await user.type(screen.getByLabelText("Confirm new password"), "new-secret");
    await user.click(screen.getByRole("button", { name: "Change password" }));

    await waitFor(() => expect(changes).toHaveLength(1));
    expect(changes[0]).toEqual({ current_password: "old-secret", new_password: "new-secret" });
    expect(await screen.findByText("Password changed")).toBeInTheDocument();
  });

  it("blocks the password change when the confirmation does not match", async () => {
    const changes: PasswordChange[] = [];
    server.use(
      http.post("*/api/v1/auth/password", async ({ request }) => {
        changes.push((await request.json()) as PasswordChange);
        return HttpResponse.json({ ok: true });
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<Settings />, { route: "/settings" });
    await user.type(await screen.findByLabelText("Current password"), "old-secret");
    await user.type(screen.getByLabelText("New password"), "new-secret");
    await user.type(screen.getByLabelText("Confirm new password"), "different");

    expect(screen.getByText("Passwords do not match")).toBeInTheDocument();
    const submit = screen.getByRole("button", { name: "Change password" });
    expect(submit).toBeDisabled();
    await user.click(submit);
    expect(changes).toEqual([]);
  });

  it("shows the API detail when the current password is wrong", async () => {
    server.use(
      http.post("*/api/v1/auth/password", () =>
        HttpResponse.json({ detail: "Current password is incorrect" }, { status: 403 }),
      ),
    );
    const user = userEvent.setup();
    renderWithProviders(<Settings />, { route: "/settings" });
    await user.type(await screen.findByLabelText("Current password"), "wrong");
    await user.type(screen.getByLabelText("New password"), "new-secret");
    await user.type(screen.getByLabelText("Confirm new password"), "new-secret");
    await user.click(screen.getByRole("button", { name: "Change password" }));

    expect(await screen.findByText(/Current password is incorrect/)).toBeInTheDocument();
    expect(screen.queryByText("Password changed")).not.toBeInTheDocument();
  });

  it("regenerates the API key after confirmation and shows it once", async () => {
    let regenerated = 0;
    server.use(
      http.post("*/api/v1/auth/api-key/regenerate", () => {
        regenerated += 1;
        return HttpResponse.json({ api_key: "new-key-abc" });
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<Settings />, { route: "/settings" });
    await screen.findByLabelText("Enabled");
    expect(screen.getByText(/shown only once/i)).toBeInTheDocument();
    expect(screen.queryByLabelText("New API key")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Regenerate API key" }));
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent("The current key stops working immediately.");
    expect(regenerated).toBe(0);

    await user.click(within(dialog).getByRole("button", { name: "Regenerate" }));
    const field = await screen.findByLabelText("New API key");
    expect(field).toHaveValue("new-key-abc");
    expect(field).toHaveAttribute("readonly");
    expect(regenerated).toBe(1);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.getByText(/will not be shown again/i)).toBeInTheDocument();
  });
});
