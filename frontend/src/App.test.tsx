import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { beforeEach, describe, expect, it } from "vitest";
import App from "./App";
import { dashboardFixture } from "./test/fixtures";
import { server } from "./test/server";

const SIDEBAR = [
  "Dashboard",
  "Findings",
  "Automation",
  "Activity log",
  "Servers",
  "Instances",
  "Notifications",
  "Settings",
  "Logs",
];

function status(needsUser: boolean) {
  return http.get("*/api/v1/setup/status", () =>
    HttpResponse.json({
      needs_user: needsUser,
      plex_connected: false,
      home_server_set: false,
      instances_count: 0,
    }),
  );
}

const unauthenticated = http.get("*/api/v1/auth/me", () =>
  HttpResponse.json({ detail: "Not authenticated" }, { status: 401 }),
);
const authenticated = http.get("*/api/v1/auth/me", () => HttpResponse.json({ principal: "admin" }));

function dashboard(configError: string | null = null) {
  return http.get("*/api/v1/dashboard", () =>
    HttpResponse.json(dashboardFixture({ config_error: configError })),
  );
}

function renderAt(path: string) {
  window.history.pushState({}, "", path);
  return render(<App />);
}

beforeEach(() => window.history.pushState({}, "", "/"));

describe("App routing", () => {
  it("sends a fresh install to the setup wizard", async () => {
    server.use(status(true), unauthenticated);
    renderAt("/findings");
    expect(await screen.findByRole("heading", { name: "Setup" })).toBeInTheDocument();
    expect(window.location.pathname).toBe("/setup");
  });

  it("sends an unauthenticated user to login", async () => {
    server.use(status(false), unauthenticated);
    renderAt("/findings");
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(window.location.pathname).toBe("/login");
  });

  it("shows the layout with every sidebar link in order when logged in", async () => {
    server.use(status(false), authenticated, dashboard());
    renderAt("/");
    const nav = await screen.findByRole("navigation", { name: "Main" });
    const links = within(nav).getAllByRole("link");
    expect(links.map((l) => l.textContent)).toEqual(SIDEBAR);
    expect(links[0]).toHaveAttribute("href", "/");
    expect(screen.getByRole("heading", { name: "Dashboard" })).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("renders the page for nested routes", async () => {
    server.use(status(false), authenticated, dashboard());
    renderAt("/findings/12");
    expect(await screen.findByText("Findings — coming soon")).toBeInTheDocument();
  });

  it("shows a banner when the config on disk is invalid", async () => {
    server.use(status(false), authenticated, dashboard("config.yaml: bad indentation"));
    renderAt("/");
    expect(await screen.findByRole("alert")).toHaveTextContent("config.yaml: bad indentation");
  });

  it("closes the mobile sidebar with the backdrop or Escape", async () => {
    server.use(status(false), authenticated, dashboard());
    const user = userEvent.setup();
    renderAt("/");
    const menu = await screen.findByRole("button", { name: "Menu" });

    await user.click(menu);
    expect(menu).toHaveAttribute("aria-expanded", "true");
    await user.click(screen.getByTestId("sidebar-backdrop"));
    expect(menu).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByTestId("sidebar-backdrop")).not.toBeInTheDocument();

    await user.click(menu);
    expect(menu).toHaveAttribute("aria-expanded", "true");
    await user.keyboard("{Escape}");
    expect(menu).toHaveAttribute("aria-expanded", "false");
  });

  it("redirects /login to the dashboard when already signed in", async () => {
    server.use(status(false), authenticated, dashboard());
    renderAt("/login");
    expect(await screen.findByRole("heading", { name: "Dashboard" })).toBeInTheDocument();
    expect(window.location.pathname).toBe("/");
  });

  it("returns to the setup wizard after signing in from it", async () => {
    let loggedIn = false;
    server.use(
      http.get("*/api/v1/setup/status", () =>
        HttpResponse.json({
          needs_user: false,
          plex_connected: false,
          home_server_set: false,
          instances_count: 0,
        }),
      ),
      http.get("*/api/v1/auth/me", () =>
        loggedIn
          ? HttpResponse.json({ principal: "admin" })
          : HttpResponse.json({ detail: "Not authenticated" }, { status: 401 }),
      ),
      http.post("*/api/v1/auth/login", () => {
        loggedIn = true;
        return HttpResponse.json({ username: "admin" });
      }),
    );
    const user = userEvent.setup();
    renderAt("/setup");
    await screen.findByRole("heading", { name: "Sign in" });
    await user.type(screen.getByLabelText("Username"), "admin");
    await user.type(screen.getByLabelText("Password"), "secret");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("heading", { name: "Sign in with Plex" })).toBeInTheDocument();
    expect(window.location.pathname).toBe("/setup");
  });

  it("logs in and lands on the dashboard", async () => {
    let loggedIn = false;
    server.use(
      status(false),
      dashboard(),
      http.get("*/api/v1/auth/me", () =>
        loggedIn
          ? HttpResponse.json({ principal: "admin" })
          : HttpResponse.json({ detail: "Not authenticated" }, { status: 401 }),
      ),
      http.post("*/api/v1/auth/login", async ({ request }) => {
        const body = (await request.json()) as { username: string; password: string };
        if (body.password !== "secret") {
          return HttpResponse.json({ detail: "Invalid username or password" }, { status: 401 });
        }
        loggedIn = true;
        return HttpResponse.json({ username: body.username });
      }),
    );
    const user = userEvent.setup();
    renderAt("/");
    await screen.findByRole("heading", { name: "Sign in" });

    await user.type(screen.getByLabelText("Username"), "admin");
    await user.type(screen.getByLabelText("Password"), "wrong");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Invalid username or password");

    await user.clear(screen.getByLabelText("Password"));
    await user.type(screen.getByLabelText("Password"), "secret");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("heading", { name: "Dashboard" })).toBeInTheDocument();
    expect(window.location.pathname).toBe("/");
  });

  it("logs out back to the login page", async () => {
    let loggedIn = true;
    server.use(
      status(false),
      dashboard(),
      http.get("*/api/v1/auth/me", () =>
        loggedIn
          ? HttpResponse.json({ principal: "admin" })
          : HttpResponse.json({ detail: "Not authenticated" }, { status: 401 }),
      ),
      http.post("*/api/v1/auth/logout", () => {
        loggedIn = false;
        return HttpResponse.json({ ok: true });
      }),
    );
    const user = userEvent.setup();
    renderAt("/");
    await user.click(await screen.findByRole("button", { name: "Log out" }));
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
  });

  it("goes to login when a session expires mid-use", async () => {
    let loggedIn = true;
    server.use(
      status(false),
      http.get("*/api/v1/auth/me", () =>
        loggedIn
          ? HttpResponse.json({ principal: "admin" })
          : HttpResponse.json({ detail: "Not authenticated" }, { status: 401 }),
      ),
      http.get("*/api/v1/dashboard", () => {
        loggedIn = false;
        return HttpResponse.json({ detail: "Not authenticated" }, { status: 401 });
      }),
    );
    renderAt("/");
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(window.location.pathname).toBe("/login");
  });
});
