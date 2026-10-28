import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { beforeEach, describe, expect, it } from "vitest";
import {
  MASK,
  type NotificationConnectionIn,
  type NotificationConnectionWithLast,
  type NotificationHistoryEntry,
  type NotifyEvents,
} from "../api/types";
import { instanceFixture } from "../test/fixtures";
import { renderWithProviders } from "../test/render";
import { server } from "../test/server";
import { Notifications } from "./Notifications";

const NO_EVENTS: NotifyEvents = {
  new_findings: false,
  new_findings_types: [],
  new_findings_instances: [],
  automation_actions: false,
  automation_deletes: false,
  finding_lost: false,
  scan_problems: false,
  app_errors: false,
};

function emailConn(): NotificationConnectionWithLast {
  return {
    id: "n-1",
    name: "Mail",
    type: "email",
    enabled: true,
    events: { ...NO_EVENTS, new_findings: true, finding_lost: true },
    digest: { mode: "weekly", time: "08:30", weekday: 2, cron: null },
    host: "smtp.example.com",
    port: 587,
    tls: "starttls",
    username: "me",
    password: MASK,
    from_addr: "overlaparr@example.com",
    to: ["a@example.com", "b@example.com"],
    last_sent: new Date(Date.now() - 2 * 3600_000).toISOString(),
  };
}

function discordConn(): NotificationConnectionWithLast {
  return {
    id: "n-2",
    name: "Chat",
    type: "discord",
    enabled: false,
    events: NO_EVENTS,
    digest: { mode: "off", time: "09:00", weekday: 0, cron: null },
    webhook_url: MASK,
    last_sent: null,
  };
}

let connections: NotificationConnectionWithLast[];
let history: NotificationHistoryEntry[];
let patches: { id: string; body: unknown }[];
let posts: NotificationConnectionIn[];
let puts: { id: string; body: NotificationConnectionIn }[];
let deletes: string[];
let testResult: { ok: true } | { ok: false; error: string };

beforeEach(() => {
  connections = [emailConn(), discordConn()];
  history = [
    {
      ts: "2026-10-27T10:00:00Z",
      connection_id: "n-1",
      connection_name: "Mail",
      event: "new_findings",
      success: false,
      error: "SMTP auth failed",
    },
  ];
  patches = [];
  posts = [];
  puts = [];
  deletes = [];
  testResult = { ok: true };
  server.use(
    http.get("*/api/v1/notifications", () => HttpResponse.json(connections)),
    http.get("*/api/v1/notifications/history", () => HttpResponse.json(history)),
    http.get("*/api/v1/instances", () => HttpResponse.json([instanceFixture()])),
    http.patch("*/api/v1/notifications/:id", async ({ params, request }) => {
      const body = (await request.json()) as Partial<NotificationConnectionWithLast>;
      patches.push({ id: String(params.id), body });
      connections = connections.map((c) =>
        c.id === params.id ? ({ ...c, ...body } as NotificationConnectionWithLast) : c,
      );
      return HttpResponse.json(connections.find((c) => c.id === params.id));
    }),
    http.post("*/api/v1/notifications", async ({ request }) => {
      const body = (await request.json()) as NotificationConnectionIn;
      posts.push(body);
      return HttpResponse.json({ ...body, id: "n-new" }, { status: 201 });
    }),
    http.put("*/api/v1/notifications/:id", async ({ params, request }) => {
      const body = (await request.json()) as NotificationConnectionIn;
      puts.push({ id: String(params.id), body });
      return HttpResponse.json({ ...body, id: params.id });
    }),
    http.delete("*/api/v1/notifications/:id", ({ params }) => {
      deletes.push(String(params.id));
      connections = connections.filter((c) => c.id !== params.id);
      return new HttpResponse(null, { status: 204 });
    }),
    http.post("*/api/v1/notifications/:id/test", () => HttpResponse.json(testResult)),
  );
});

function row(name: RegExp) {
  return within(screen.getByRole("row", { name }));
}

describe("Notifications", () => {
  it("lists connections with triggers, digest and last sent", async () => {
    renderWithProviders(<Notifications />, { route: "/notifications" });
    expect(await screen.findByRole("heading", { name: "Notifications" })).toBeInTheDocument();
    await screen.findByRole("row", { name: /Mail/ });

    const mail = row(/Mail/);
    expect(mail.getByText("Email")).toBeInTheDocument();
    expect(mail.getByText("New findings, Finding lost")).toBeInTheDocument();
    expect(mail.getByText("Weekly on Wednesday at 08:30")).toBeInTheDocument();
    expect(mail.getByText("2 hours ago")).toBeInTheDocument();
    expect(mail.getByRole("switch", { name: "Enable Mail" })).toHaveAttribute(
      "aria-checked",
      "true",
    );

    const chat = row(/Chat/);
    expect(chat.getByText("Discord")).toBeInTheDocument();
    expect(chat.getByText("None")).toBeInTheDocument();
    expect(chat.getByText("Off")).toBeInTheDocument();
    expect(chat.getByText("Never")).toBeInTheDocument();
  });

  it("enabling sends PATCH {enabled}", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Notifications />, { route: "/notifications" });
    await user.click(await screen.findByRole("switch", { name: "Enable Chat" }));
    await waitFor(() => expect(patches).toEqual([{ id: "n-2", body: { enabled: true } }]));
  });

  it("Test shows the success toast", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Notifications />, { route: "/notifications" });
    await screen.findByRole("row", { name: /Mail/ });
    await user.click(row(/Mail/).getByRole("button", { name: "Test" }));
    expect(await screen.findByText("Test message sent")).toBeInTheDocument();
  });

  it("Test shows the failure error text", async () => {
    testResult = { ok: false, error: "SMTP auth failed: 535" };
    const user = userEvent.setup();
    renderWithProviders(<Notifications />, { route: "/notifications" });
    await screen.findByRole("row", { name: /Mail/ });
    await user.click(row(/Mail/).getByRole("button", { name: "Test" }));
    expect(await screen.findByText(/SMTP auth failed: 535/)).toBeInTheDocument();
  });

  it("adds a disabled Discord connection with all events off", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Notifications />, { route: "/notifications" });
    await screen.findByRole("row", { name: /Mail/ });
    await user.click(screen.getByRole("button", { name: "Add connection" }));
    const dialog = await screen.findByRole("dialog");

    expect(within(dialog).getByLabelText("Name")).toHaveFocus();
    await user.type(within(dialog).getByLabelText("Name"), "Ops");
    await user.selectOptions(within(dialog).getByLabelText("Type"), "discord");
    await user.type(within(dialog).getByLabelText("Webhook URL"), "https://discord.test/hook");
    await user.click(within(dialog).getByRole("button", { name: "Save" }));

    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0]).toEqual({
      type: "discord",
      name: "Ops",
      enabled: false,
      webhook_url: "https://discord.test/hook",
      events: NO_EVENTS,
      digest: { mode: "off", time: "09:00", weekday: 0, cron: null },
    });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("new findings sub-options and webhook headers", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Notifications />, { route: "/notifications" });
    await screen.findByRole("row", { name: /Mail/ });
    await user.click(screen.getByRole("button", { name: "Add connection" }));
    const dialog = within(await screen.findByRole("dialog"));

    await user.type(dialog.getByLabelText("Name"), "Hook");
    await user.selectOptions(dialog.getByLabelText("Type"), "webhook");
    await user.type(dialog.getByLabelText("URL"), "https://hook.test");
    await user.selectOptions(dialog.getByLabelText("Method"), "PUT");
    await user.type(dialog.getByLabelText("Headers"), "X-Token: abc{Enter}{Enter}broken");
    await user.click(dialog.getByRole("checkbox", { name: "New findings after a scan" }));
    await user.click(dialog.getByRole("checkbox", { name: "On disk" }));
    await user.click(await dialog.findByRole("checkbox", { name: "Sonarr" }));
    await user.selectOptions(dialog.getByLabelText("Digest"), "daily");
    await user.clear(dialog.getByLabelText("Time"));
    await user.type(dialog.getByLabelText("Time"), "07:15");

    await user.click(dialog.getByRole("button", { name: "Save" }));
    expect(await dialog.findByText(/Header line 3/)).toBeInTheDocument();
    expect(posts).toHaveLength(0);

    await user.clear(dialog.getByLabelText("Headers"));
    await user.type(dialog.getByLabelText("Headers"), "X-Token: abc{Enter}{Enter}Accept: a:b");
    await user.click(dialog.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0]).toMatchObject({
      type: "webhook",
      url: "https://hook.test",
      method: "PUT",
      headers: { "X-Token": "abc", Accept: "a:b" },
      events: { ...NO_EVENTS, new_findings: true, new_findings_types: ["on_disk"], new_findings_instances: ["i-1"] },
      digest: { mode: "daily", time: "07:15", weekday: 0, cron: null },
    });
  });

  it("editing an email connection PUTs with the masked password unchanged", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Notifications />, { route: "/notifications" });
    await screen.findByRole("row", { name: /Mail/ });
    await user.click(row(/Mail/).getByRole("button", { name: "Edit" }));
    const dialog = within(await screen.findByRole("dialog"));

    expect(dialog.getByLabelText("Host")).toHaveValue("smtp.example.com");
    expect(dialog.getByLabelText("To")).toHaveValue("a@example.com, b@example.com");
    expect(dialog.getByLabelText("Weekday")).toHaveValue("2");
    await user.clear(dialog.getByLabelText("Host"));
    await user.type(dialog.getByLabelText("Host"), "smtp2.example.com");
    await user.click(dialog.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(puts).toHaveLength(1));
    expect(puts[0].id).toBe("n-1");
    expect(puts[0].body).toEqual({
      type: "email",
      name: "Mail",
      enabled: true,
      host: "smtp2.example.com",
      port: 587,
      tls: "starttls",
      username: "me",
      password: MASK,
      from_addr: "overlaparr@example.com",
      to: ["a@example.com", "b@example.com"],
      events: { ...NO_EVENTS, new_findings: true, finding_lost: true },
      digest: { mode: "weekly", time: "08:30", weekday: 2, cron: null },
    });
  });

  it("editing an Apprise connection warns that URLs are re-entered together", async () => {
    connections = [
      {
        id: "n-3",
        name: "Apprise",
        type: "apprise",
        enabled: true,
        events: NO_EVENTS,
        digest: { mode: "off", time: "09:00", weekday: 0, cron: null },
        urls: [MASK, MASK],
        last_sent: null,
      },
    ];
    const user = userEvent.setup();
    renderWithProviders(<Notifications />, { route: "/notifications" });
    await screen.findByRole("row", { name: /Apprise/ });
    await user.click(row(/Apprise/).getByRole("button", { name: "Edit" }));
    const dialog = within(await screen.findByRole("dialog"));
    expect(dialog.getByLabelText("URLs")).toHaveValue(`${MASK}
${MASK}`);
    expect(
      dialog.getByText(/Changing any URL requires re-entering all of them\./),
    ).toBeInTheDocument();
  });

  it("delete confirms and sends DELETE", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Notifications />, { route: "/notifications" });
    await screen.findByRole("row", { name: /Chat/ });
    await user.click(row(/Chat/).getByRole("button", { name: "Delete" }));
    const dialog = within(await screen.findByRole("dialog", { name: "Delete Chat?" }));
    expect(dialog.getByText(/config\.yaml/)).toBeInTheDocument();
    await user.click(dialog.getByRole("button", { name: "Delete connection" }));
    await waitFor(() => expect(deletes).toEqual(["n-2"]));
    await waitFor(() => expect(screen.queryByRole("row", { name: /Chat/ })).not.toBeInTheDocument());
  });

  it("history tab loads and renders entries", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Notifications />, { route: "/notifications" });
    await screen.findByRole("row", { name: /Mail/ });
    await user.click(screen.getByRole("button", { name: "History" }));
    expect(screen.getByRole("button", { name: "History" })).toHaveAttribute("aria-pressed", "true");
    await screen.findByRole("row", { name: /SMTP auth failed/ });
    const entry = row(/SMTP auth failed/);
    expect(entry.getByText("Mail")).toBeInTheDocument();
    expect(entry.getByText("new_findings")).toBeInTheDocument();
    expect(entry.getByText("Failed")).toBeInTheDocument();
  });
});
