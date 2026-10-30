import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createQueryClient } from "../api/queryClient";
import type { Instance, SetupStatus } from "../api/types";
import { instanceFixture } from "../test/fixtures";
import { server } from "../test/server";
import { Setup } from "./Setup";

const FRESH: SetupStatus = {
  needs_user: true,
  plex_connected: false,
  home_server_set: false,
  instances_count: 0,
};

const SERVERS = [
  {
    machine_id: "m-home",
    name: "My Server",
    label: "My Server",
    enabled: true,
    is_home: false,
    is_new: true,
    status: "online",
    reachability: "online",
    last_indexed_at: null,
    last_attempt_at: null,
    movie_count: 0,
    show_count: 0,
    unmatchable_count: 0,
  },
  {
    machine_id: "m-friend",
    name: "Friend",
    label: "Friend",
    enabled: true,
    is_home: false,
    is_new: true,
    status: "online",
    reachability: "online",
    last_indexed_at: null,
    last_attempt_at: null,
    movie_count: 0,
    show_count: 0,
    unmatchable_count: 0,
  },
];

let status: SetupStatus;
let instances: Instance[];

function statusHandler() {
  return http.get("*/api/v1/setup/status", () => HttpResponse.json(status));
}

const loggedIn = http.get("*/api/v1/auth/me", () => HttpResponse.json({ principal: "admin" }));
const loggedOut = http.get("*/api/v1/auth/me", () =>
  HttpResponse.json({ detail: "Not authenticated" }, { status: 401 }),
);
const listInstances = http.get("*/api/v1/instances", () => HttpResponse.json(instances));
let instancePosts = 0;
const saveInstance = http.post("*/api/v1/instances", async ({ request }) => {
  instancePosts += 1;
  const body = (await request.json()) as Omit<Instance, "id">;
  const saved: Instance = { ...body, id: `i${instances.length + 1}`, api_key: "********" };
  instances = [...instances, saved];
  status = { ...status, instances_count: instances.length };
  return HttpResponse.json(saved);
});

async function fillInstance(user: ReturnType<typeof userEvent.setup>, name = "Sonarr") {
  await screen.findByRole("heading", { name: "Add your Sonarr and Radarr instances" });
  await user.type(await screen.findByLabelText("Name"), name);
  await user.type(screen.getByLabelText("URL"), "http://sonarr:8989");
  await user.type(screen.getByLabelText("API key"), "key");
}

const refresh = http.post("*/api/v1/servers/refresh", () => HttpResponse.json(SERVERS));

function renderSetup(path = "/setup") {
  const client = createQueryClient();
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/setup" element={<Setup plexPollMs={10} scanPollMs={10} />} />
          <Route path="/" element={<p>Dashboard page</p>} />
          <Route path="/login" element={<p>Login page</p>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  status = { ...FRESH };
  instances = [];
  instancePosts = 0;
  vi.spyOn(window, "open").mockReturnValue(null);
});
afterEach(() => vi.restoreAllMocks());

describe("Setup wizard", () => {
  it("creates the user and advances to the Plex step", async () => {
    let created: unknown = null;
    let settingsBody: unknown = null;
    server.use(
      statusHandler(),
      http.post("*/api/v1/setup/user", async ({ request }) => {
        created = await request.json();
        status = { ...status, needs_user: false };
        return HttpResponse.json({ username: "admin" });
      }),
      http.put("*/api/v1/settings", async ({ request }) => {
        settingsBody = await request.json();
        await settingsGate;
        return HttpResponse.json({});
      }),
    );
    let release!: () => void;
    const settingsGate = new Promise<void>((resolve) => (release = resolve));
    const user = userEvent.setup();
    renderSetup();

    await user.type(await screen.findByLabelText("Username"), "admin");
    await user.type(screen.getByLabelText("Password"), "hunter22");
    expect(screen.getByText(/reverse proxy/i)).toBeInTheDocument();
    await user.click(screen.getByLabelText("Disable login for local network"));
    await user.click(screen.getByRole("button", { name: "Create account" }));

    // The account form stays locked until the local-network setting is saved too.
    await waitFor(() => expect(settingsBody).not.toBeNull());
    expect(screen.getByRole("button", { name: "Create account" })).toBeDisabled();
    release();
    expect(await screen.findByRole("heading", { name: "Sign in with Plex" })).toBeInTheDocument();
    expect(created).toEqual({ username: "admin", password: "hunter22" });
    expect(settingsBody).toEqual({ auth: { local_bypass: true } });
  });

  it("shows the error detail when the user cannot be created", async () => {
    server.use(
      statusHandler(),
      http.post("*/api/v1/setup/user", () =>
        HttpResponse.json({ detail: "password must be at least 8 characters" }, { status: 422 }),
      ),
    );
    const user = userEvent.setup();
    renderSetup();
    await user.type(await screen.findByLabelText("Username"), "admin");
    await user.type(screen.getByLabelText("Password"), "x");
    await user.click(screen.getByRole("button", { name: "Create account" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "password must be at least 8 characters",
    );
    expect(screen.queryByRole("heading", { name: "Sign in with Plex" })).not.toBeInTheDocument();
  });

  it("polls the Plex PIN until connected, then shows the home server step", async () => {
    status = { ...FRESH, needs_user: false };
    let polls = 0;
    server.use(
      statusHandler(),
      loggedIn,
      refresh,
      http.post("*/api/v1/plex/pin", () =>
        HttpResponse.json({ id: 42, code: "abcd", auth_url: "https://app.plex.tv/auth#?code=abcd" }),
      ),
      http.get("*/api/v1/plex/pin/42", () => {
        polls += 1;
        return HttpResponse.json({ connected: polls >= 2 });
      }),
    );
    const popup = { closed: false, opener: {} as unknown, location: { href: "" }, close: vi.fn() };
    vi.mocked(window.open).mockReturnValue(popup as unknown as Window);
    const user = userEvent.setup();
    renderSetup();

    await user.click(await screen.findByRole("button", { name: "Sign in with Plex" }));
    expect(window.open).toHaveBeenCalled();
    await waitFor(() => expect(popup.location.href).toBe("https://app.plex.tv/auth#?code=abcd"));
    expect(popup.opener).toBeNull();
    expect(await screen.findByRole("link", { name: /open plex sign-in/i })).toHaveAttribute(
      "href",
      "https://app.plex.tv/auth#?code=abcd",
    );

    expect(await screen.findByRole("heading", { name: "Choose your home server" })).toBeInTheDocument();
    expect(polls).toBe(2);
    expect(popup.close).toHaveBeenCalled();
    expect(await screen.findByLabelText(/My Server/)).toBeInTheDocument();
  });

  it("accepts a pasted Plex token", async () => {
    status = { ...FRESH, needs_user: false };
    let token: unknown = null;
    server.use(
      statusHandler(),
      loggedIn,
      refresh,
      http.put("*/api/v1/plex/token", async ({ request }) => {
        token = await request.json();
        return HttpResponse.json({ connected: true });
      }),
    );
    const user = userEvent.setup();
    renderSetup();

    await user.click(await screen.findByRole("button", { name: "Paste a token instead" }));
    await user.type(screen.getByLabelText("Plex token"), "tok-123");
    await user.click(screen.getByRole("button", { name: "Save token" }));

    expect(await screen.findByRole("heading", { name: "Choose your home server" })).toBeInTheDocument();
    expect(token).toEqual({ token: "tok-123" });
  });

  it("sets the home server and moves to the instance step", async () => {
    status = { ...FRESH, needs_user: false, plex_connected: true };
    let home: unknown = null;
    server.use(
      statusHandler(),
      loggedIn,
      listInstances,
      refresh,
      http.put("*/api/v1/servers/home", async ({ request }) => {
        home = await request.json();
        return HttpResponse.json({ home_server: "m-home", scan_id: null });
      }),
    );
    const user = userEvent.setup();
    renderSetup();

    await user.click(await screen.findByLabelText(/My Server/));
    await user.click(screen.getByRole("button", { name: "Continue" }));
    expect(await screen.findByRole("heading", { name: "Add your Sonarr and Radarr instances" })).toBeInTheDocument();
    expect(home).toEqual({ machine_id: "m-home" });
  });

  it("shows the error detail when the instance test fails", async () => {
    status = { ...FRESH, needs_user: false, plex_connected: true, home_server_set: true };
    let tested: unknown = null;
    server.use(
      statusHandler(),
      loggedIn,
      listInstances,
      http.post("*/api/v1/instances/test", async ({ request }) => {
        tested = await request.json();
        return HttpResponse.json({ ok: false, error: "401 Unauthorized: invalid API key" });
      }),
    );
    const user = userEvent.setup();
    renderSetup();

    await screen.findByRole("heading", { name: "Add your Sonarr and Radarr instances" });
    await user.type(await screen.findByLabelText("Name"), "Sonarr");
    await user.type(screen.getByLabelText("URL"), "http://sonarr:8989");
    await user.type(screen.getByLabelText("API key"), "bad-key");
    await user.click(screen.getByRole("button", { name: "Test" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("401 Unauthorized: invalid API key");
    expect(tested).toEqual({ type: "sonarr", url: "http://sonarr:8989", api_key: "bad-key" });
  });

  it("saves an instance, then finishes by starting a scan and going to the dashboard", async () => {
    status = { ...FRESH, needs_user: false, plex_connected: true, home_server_set: true };
    let saved: Record<string, unknown> | null = null;
    let scanned = false;
    server.use(
      statusHandler(),
      loggedIn,
      listInstances,
      http.post("*/api/v1/instances/test", () => HttpResponse.json({ ok: true, version: "4.0.1" })),
      http.post("*/api/v1/instances", async ({ request }) => {
        saved = (await request.json()) as Record<string, unknown>;
        instances = [instanceFixture({ id: "i1", name: "Radarr", type: "radarr" })];
        status = { ...status, instances_count: 1 };
        return HttpResponse.json(instances[0]);
      }),
      http.post("*/api/v1/scan", () => {
        scanned = true;
        return HttpResponse.json({ scan_id: 2 }, { status: 202 });
      }),
    );
    const user = userEvent.setup();
    renderSetup();

    await screen.findByRole("heading", { name: "Add your Sonarr and Radarr instances" });
    await user.type(await screen.findByLabelText("Name"), "Radarr");
    await user.selectOptions(screen.getByLabelText("Type"), "radarr");
    await user.type(screen.getByLabelText("URL"), "http://radarr:7878");
    await user.type(screen.getByLabelText("API key"), "good-key");
    await user.click(screen.getByRole("button", { name: "Test" }));
    expect(await screen.findByText(/Connected.*4\.0\.1/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Save" }));
    await user.click(await screen.findByRole("button", { name: "Continue" }));

    await user.click(await screen.findByRole("button", { name: "Finish and run first scan" }));
    expect(await screen.findByText("Dashboard page")).toBeInTheDocument();
    expect(scanned).toBe(true);
    expect(saved).toMatchObject({
      name: "Radarr",
      type: "radarr",
      url: "http://radarr:7878",
      api_key: "good-key",
      enabled: true,
    });
  });

  it("waits for the running scan, then scans again to include the new instance", async () => {
    status = { ...FRESH, needs_user: false, plex_connected: true, home_server_set: true };
    let scanPosts = 0;
    let statusPolls = 0;
    server.use(
      statusHandler(),
      loggedIn,
      listInstances,
      saveInstance,
      http.post("*/api/v1/scan", () => {
        scanPosts += 1;
        return scanPosts === 1
          ? HttpResponse.json({ detail: "Scan already running" }, { status: 409 })
          : HttpResponse.json({ scan_id: 3 }, { status: 202 });
      }),
      http.get("*/api/v1/scan/status", () => {
        statusPolls += 1;
        return HttpResponse.json({ progress: { running: statusPolls < 3 }, runs: [] });
      }),
    );
    const user = userEvent.setup();
    renderSetup();
    await fillInstance(user);
    await user.click(screen.getByRole("button", { name: "Save" }));
    await user.click(await screen.findByRole("button", { name: "Continue" }));
    await user.click(await screen.findByRole("button", { name: "Finish and run first scan" }));

    expect(
      await screen.findByText(
        "A scan is already running and doesn't include your instances — waiting for it to finish, then starting another…",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText(/home server/)).not.toBeInTheDocument();
    expect(await screen.findByText("Dashboard page")).toBeInTheDocument();
    expect(statusPolls).toBe(3);
    expect(scanPosts).toBe(2);
  });

  it("can leave for the dashboard while waiting for the running scan", async () => {
    status = { ...FRESH, needs_user: false, plex_connected: true, home_server_set: true };
    server.use(
      statusHandler(),
      loggedIn,
      listInstances,
      saveInstance,
      http.post("*/api/v1/scan", () =>
        HttpResponse.json({ detail: "Scan already running" }, { status: 409 }),
      ),
      http.get("*/api/v1/scan/status", () =>
        HttpResponse.json({ progress: { running: true }, runs: [] }),
      ),
    );
    const user = userEvent.setup();
    renderSetup();
    await fillInstance(user);
    await user.click(screen.getByRole("button", { name: "Save" }));
    await user.click(await screen.findByRole("button", { name: "Continue" }));
    await user.click(await screen.findByRole("button", { name: "Finish and run first scan" }));
    expect(await screen.findByText(/run a scan once the current one finishes/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Go to dashboard now" }));
    expect(await screen.findByText("Dashboard page")).toBeInTheDocument();
  });

  it("adds several instances, each as a new one, then continues", async () => {
    status = { ...FRESH, needs_user: false, plex_connected: true, home_server_set: true };
    let listFetches = 0;
    let puts = 0;
    server.use(
      statusHandler(),
      loggedIn,
      http.get("*/api/v1/instances", () => {
        listFetches += 1;
        return HttpResponse.json(instances);
      }),
      saveInstance,
      http.put("*/api/v1/instances/:id", () => {
        puts += 1;
        return HttpResponse.json({ detail: "unexpected" }, { status: 400 });
      }),
    );
    const user = userEvent.setup();
    renderSetup();

    expect(await screen.findByRole("button", { name: "Skip for now" })).toBeInTheDocument();
    await fillInstance(user, "TV");
    await user.click(screen.getByRole("button", { name: "Save" }));

    // The list is the server's, refetched after the save.
    const list = await screen.findByRole("list", { name: "Added instances" });
    await waitFor(() => expect(listFetches).toBe(2));
    expect(within(list).getAllByRole("listitem").map((li) => li.textContent)).toEqual([
      "TVSonarr",
    ]);
    expect(screen.queryByLabelText("Name")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Skip for now" })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Add another" }));
    expect(screen.getByLabelText("Name")).toHaveValue("");
    expect(screen.getByLabelText("URL")).toHaveValue("");
    expect(screen.queryByRole("button", { name: "Skip for now" })).not.toBeInTheDocument();
    await user.type(screen.getByLabelText("Name"), "Movies");
    await user.selectOptions(screen.getByLabelText("Type"), "radarr");
    await user.type(screen.getByLabelText("URL"), "http://radarr:7878");
    await user.type(screen.getByLabelText("API key"), "key2");
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(
        within(screen.getByRole("list", { name: "Added instances" }))
          .getAllByRole("listitem")
          .map((li) => li.textContent),
      ).toEqual(["TVSonarr", "MoviesRadarr"]),
    );
    expect(instancePosts).toBe(2);
    expect(puts).toBe(0);

    await user.click(screen.getByRole("button", { name: "Continue" }));
    expect(
      await screen.findByRole("button", { name: "Finish and run first scan" }),
    ).toBeInTheDocument();
  });

  it("shows a loading line, not the form or Skip, while the instance list loads", async () => {
    status = { ...FRESH, needs_user: false, plex_connected: true, home_server_set: true };
    let release!: () => void;
    const gate = new Promise<void>((resolve) => (release = resolve));
    server.use(
      statusHandler(),
      loggedIn,
      http.get("*/api/v1/instances", async () => {
        await gate;
        return HttpResponse.json(instances);
      }),
    );
    renderSetup();

    expect(await screen.findByText("Loading your instances…")).toBeInTheDocument();
    expect(screen.getByText("4. Instances")).toHaveAttribute("aria-current", "step");
    expect(screen.queryByLabelText("Name")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Skip for now" })).not.toBeInTheDocument();
    release();
    expect(await screen.findByLabelText("Name")).toBeInTheDocument();
    expect(screen.queryByText("Loading your instances…")).not.toBeInTheDocument();
  });

  it("lists instances that already exist and offers Continue on load", async () => {
    status = { ...FRESH, needs_user: false, plex_connected: true, home_server_set: true };
    instances = [instanceFixture({ id: "i1", name: "TV", type: "sonarr" })];
    server.use(statusHandler(), loggedIn, listInstances);
    renderSetup();

    const list = await screen.findByRole("list", { name: "Added instances" });
    expect(within(list).getAllByRole("listitem").map((li) => li.textContent)).toEqual([
      "TVSonarr",
    ]);
    expect(screen.getByRole("button", { name: "Continue" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add another" })).toBeInTheDocument();
    expect(screen.queryByLabelText("Name")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Skip for now" })).not.toBeInTheDocument();
  });

  it("shows the error and retries when the instance list fails to load", async () => {
    status = { ...FRESH, needs_user: false, plex_connected: true, home_server_set: true };
    let fail = true;
    server.use(
      statusHandler(),
      loggedIn,
      http.get("*/api/v1/instances", () =>
        fail
          ? HttpResponse.json({ detail: "Not allowed" }, { status: 403 })
          : HttpResponse.json(instances),
      ),
    );
    const user = userEvent.setup();
    renderSetup();

    expect(await screen.findByRole("alert")).toHaveTextContent("Not allowed");
    expect(screen.queryByLabelText("Name")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Skip for now" })).not.toBeInTheDocument();
    fail = false;
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByLabelText("Name")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("lets the instance step be skipped and treats an already running scan as fine", async () => {
    status = { ...FRESH, needs_user: false, plex_connected: true, home_server_set: true };
    server.use(
      statusHandler(),
      loggedIn,
      listInstances,
      http.post("*/api/v1/scan", () =>
        HttpResponse.json({ detail: "Scan already running" }, { status: 409 }),
      ),
    );
    const user = userEvent.setup();
    renderSetup();
    expect(await screen.findByText(/add more later on the Instances page/)).toBeInTheDocument();
    await user.click(await screen.findByRole("button", { name: "Skip for now" }));
    await user.click(await screen.findByRole("button", { name: "Finish and run first scan" }));
    expect(await screen.findByText("Dashboard page")).toBeInTheDocument();
  });

  it("redirects to the dashboard when setup is already complete", async () => {
    status = { needs_user: false, plex_connected: true, home_server_set: true, instances_count: 2 };
    server.use(statusHandler());
    renderSetup();
    expect(await screen.findByText("Dashboard page")).toBeInTheDocument();
  });

  it("sends a signed-out user to login when the user already exists", async () => {
    status = { ...FRESH, needs_user: false };
    server.use(statusHandler(), loggedOut);
    renderSetup();
    expect(await screen.findByText("Login page")).toBeInTheDocument();
  });

  it("resumes at the first incomplete step", async () => {
    status = { ...FRESH, needs_user: false, plex_connected: true };
    server.use(statusHandler(), loggedIn, refresh);
    renderSetup();
    expect(await screen.findByRole("heading", { name: "Choose your home server" })).toBeInTheDocument();
    await waitFor(() => expect(screen.getByLabelText(/Friend/)).toBeInTheDocument());
  });
});
