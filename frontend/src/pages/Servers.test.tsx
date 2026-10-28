import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { beforeEach, describe, expect, it } from "vitest";
import type { ServerPatch, ServerRow } from "../api/types";
import { renderWithProviders } from "../test/render";
import { server } from "../test/server";
import { Servers } from "./Servers";

function serverRowFixture(overrides: Partial<ServerRow> = {}): ServerRow {
  return {
    machine_id: "m-a",
    name: "MyPlex",
    label: "MyPlex",
    enabled: true,
    is_home: true,
    is_new: false,
    status: "online",
    reachability: "online",
    last_indexed_at: null,
    last_attempt_at: null,
    movie_count: 0,
    show_count: 0,
    unmatchable_count: 0,
    ...overrides,
  };
}

let servers: ServerRow[];
let patches: { id: string; body: ServerPatch }[];
let refreshes: number;
let homes: { machine_id: string }[];

beforeEach(() => {
  servers = [
    serverRowFixture({ movie_count: 120, show_count: 30, unmatchable_count: 4 }),
    serverRowFixture({
      machine_id: "m-b",
      name: "Friend",
      label: "",
      is_home: false,
      is_new: true,
      status: "stale",
      reachability: "stale",
      last_indexed_at: "2026-10-20T08:00:00Z",
      movie_count: 12,
      show_count: 2,
    }),
  ];
  patches = [];
  refreshes = 0;
  homes = [];
  server.use(
    http.get("*/api/v1/servers", () => HttpResponse.json(servers)),
    http.patch("*/api/v1/servers/:id", async ({ params, request }) => {
      const body = (await request.json()) as ServerPatch;
      const id = String(params.id);
      patches.push({ id, body });
      servers = servers.map((s) => {
        if (s.machine_id !== id) return s;
        const next = { ...s };
        if (body.enabled !== undefined && body.enabled !== null) {
          next.enabled = body.enabled;
          next.status = body.enabled ? next.reachability : "disabled";
        }
        if (body.label !== undefined) next.label = body.label ?? s.name;
        return next;
      });
      return HttpResponse.json(servers.find((s) => s.machine_id === id));
    }),
    http.post("*/api/v1/servers/refresh", () => {
      refreshes += 1;
      return HttpResponse.json(servers);
    }),
    http.put("*/api/v1/servers/home", async ({ request }) => {
      const body = (await request.json()) as { machine_id: string };
      homes.push(body);
      return HttpResponse.json({ home_server: body.machine_id, scan_id: 99 });
    }),
  );
});

function row(name: RegExp) {
  return within(screen.getByRole("row", { name }));
}

describe("Servers", () => {
  it("lists servers with badges, status and counts", async () => {
    renderWithProviders(<Servers />, { route: "/servers" });
    expect(await screen.findByRole("heading", { name: "Servers" })).toBeInTheDocument();
    await screen.findByText("MyPlex");

    const home = row(/MyPlex/);
    expect(home.getByText("Home")).toBeInTheDocument();
    expect(home.queryByText("New")).not.toBeInTheDocument();
    expect(home.getByText("online")).toBeInTheDocument();
    expect(home.getByText("Never")).toBeInTheDocument();
    expect(home.getByText("120")).toBeInTheDocument();
    expect(home.getByText("30")).toBeInTheDocument();
    expect(home.getByText("4")).toBeInTheDocument();

    // label "" falls back to the server name
    const friend = row(/Friend/);
    expect(friend.getByText("New")).toBeInTheDocument();
    expect(friend.queryByText("Home")).not.toBeInTheDocument();
    expect(friend.getByText("stale")).toBeInTheDocument();
    expect(friend.getByText(/ago/)).toBeInTheDocument();
    expect(friend.getByText("12")).toBeInTheDocument();
    expect(friend.getByRole("button", { name: "Make home" })).toBeInTheDocument();
    expect(home.queryByRole("button", { name: "Make home" })).not.toBeInTheDocument();
  });

  it("toggles a server off with a PATCH and refetches", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Servers />, { route: "/servers" });
    await screen.findByText("MyPlex");

    await user.click(row(/MyPlex/).getByRole("switch", { name: "Enable MyPlex" }));
    await waitFor(() => expect(patches).toEqual([{ id: "m-a", body: { enabled: false } }]));
    await waitFor(() =>
      expect(row(/MyPlex/).getByRole("switch", { name: "Enable MyPlex" })).toHaveAttribute(
        "aria-checked",
        "false",
      ),
    );
    expect(row(/MyPlex/).getByText("disabled")).toBeInTheDocument();
  });

  it("renames a server with Enter", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Servers />, { route: "/servers" });
    await screen.findByText("Friend");

    await user.click(row(/Friend/).getByRole("button", { name: "Edit label" }));
    const input = screen.getByLabelText("Label for Friend");
    await user.clear(input);
    await user.type(input, "New name{Enter}");

    await waitFor(() => expect(patches).toEqual([{ id: "m-b", body: { label: "New name" } }]));
    expect(await screen.findByText("New name")).toBeInTheDocument();
    expect(screen.queryByLabelText("Label for Friend")).not.toBeInTheDocument();
  });

  it("cancels a label edit with Escape", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Servers />, { route: "/servers" });
    await screen.findByText("Friend");

    await user.click(row(/Friend/).getByRole("button", { name: "Edit label" }));
    await user.type(screen.getByLabelText("Label for Friend"), "Nope{Escape}");
    expect(screen.queryByLabelText("Label for Friend")).not.toBeInTheDocument();
    expect(patches).toEqual([]);
  });

  it("changes the home server after confirming the rebuild warning", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Servers />, { route: "/servers" });
    await screen.findByText("Friend");

    await user.click(row(/Friend/).getByRole("button", { name: "Make home" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/Change the home server\?/)).toBeInTheDocument();
    expect(within(dialog).getByText(/deletes all findings and rebuilds/i)).toBeInTheDocument();
    expect(homes).toEqual([]);

    await user.click(within(dialog).getByRole("button", { name: "Change home server" }));
    await waitFor(() => expect(homes).toEqual([{ machine_id: "m-b" }]));
    expect(
      await screen.findByText("Home server changed — a scan is rebuilding findings."),
    ).toBeInTheDocument();
  });

  it("refreshes servers", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Servers />, { route: "/servers" });
    await screen.findByText("MyPlex");

    await user.click(screen.getByRole("button", { name: "Refresh" }));
    await waitFor(() => expect(refreshes).toBe(1));
    expect(await screen.findByText("Servers refreshed")).toBeInTheDocument();
  });

  it("shows the API detail when refreshing fails", async () => {
    server.use(
      http.post("*/api/v1/servers/refresh", () =>
        HttpResponse.json({ detail: "Plex is unreachable" }, { status: 502 }),
      ),
    );
    const user = userEvent.setup();
    renderWithProviders(<Servers />, { route: "/servers" });
    await screen.findByText("MyPlex");

    await user.click(screen.getByRole("button", { name: "Refresh" }));
    expect(await screen.findByText(/Plex is unreachable/, {}, { timeout: 5000 })).toBeInTheDocument();
  });

  it("shows a retryable error when the list fails to load", async () => {
    let attempts = 0;
    server.use(
      http.get("*/api/v1/servers", () => {
        attempts += 1;
        return attempts === 1
          ? HttpResponse.json({ detail: "Could not load servers" }, { status: 404 })
          : HttpResponse.json(servers);
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<Servers />, { route: "/servers" });

    expect(await screen.findByRole("alert")).toHaveTextContent("Could not load servers");
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("MyPlex")).toBeInTheDocument();
  });
});
