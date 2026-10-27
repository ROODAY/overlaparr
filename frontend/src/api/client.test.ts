import { http, HttpResponse } from "msw";
import { afterEach, describe, expect, it, vi } from "vitest";
import { server } from "../test/server";
import { api, ApiError, setUnauthorizedHandler } from "./client";

afterEach(() => setUnauthorizedHandler(null));

async function caught(p: Promise<unknown>): Promise<ApiError> {
  try {
    await p;
  } catch (e) {
    if (e instanceof ApiError) return e;
    throw e;
  }
  throw new Error("expected an ApiError");
}

describe("api client", () => {
  it("returns parsed JSON and sends JSON bodies", async () => {
    let sent: unknown;
    server.use(
      http.post("*/api/v1/things", async ({ request }) => {
        sent = await request.json();
        return HttpResponse.json({ id: 1 });
      }),
    );
    await expect(api.post<{ id: number }>("/things", { name: "x" })).resolves.toEqual({ id: 1 });
    expect(sent).toEqual({ name: "x" });
  });

  it("returns undefined for 204", async () => {
    server.use(http.delete("*/api/v1/rules/r1", () => new HttpResponse(null, { status: 204 })));
    await expect(api.del("/rules/r1")).resolves.toBeUndefined();
  });

  it("throws ApiError with the string detail", async () => {
    server.use(
      http.put("*/api/v1/servers/home", () =>
        HttpResponse.json({ detail: "Unknown server" }, { status: 404 }),
      ),
    );
    const err = await caught(api.put("/servers/home", { machine_id: "x" }));
    expect(err.status).toBe(404);
    expect(err.detail).toBe("Unknown server");
  });

  it("joins validation error messages into one detail", async () => {
    server.use(
      http.post("*/api/v1/instances", () =>
        HttpResponse.json(
          {
            detail: [
              { loc: ["body", "url"], msg: "Input should be a valid URL" },
              { loc: ["body", "name"], msg: "Field required" },
            ],
          },
          { status: 422 },
        ),
      ),
    );
    const err = await caught(api.post("/instances", {}));
    expect(err.status).toBe(422);
    expect(err.detail).toBe("Input should be a valid URL; Field required");
  });

  it("falls back to the status text for non-JSON errors", async () => {
    server.use(
      http.get("*/api/v1/boom", () => new HttpResponse("oops", { status: 502, statusText: "Bad Gateway" })),
    );
    const err = await caught(api.get("/boom"));
    expect(err.status).toBe(502);
    expect(err.detail).toBe("Bad Gateway");
  });

  it("calls the unauthorized handler on 401 outside auth and setup", async () => {
    const onUnauthorized = vi.fn();
    setUnauthorizedHandler(onUnauthorized);
    server.use(
      http.get("*/api/v1/dashboard", () =>
        HttpResponse.json({ detail: "Not authenticated" }, { status: 401 }),
      ),
      http.post("*/api/v1/auth/login", () =>
        HttpResponse.json({ detail: "Invalid username or password" }, { status: 401 }),
      ),
    );
    expect((await caught(api.get("/dashboard"))).status).toBe(401);
    expect(onUnauthorized).toHaveBeenCalledTimes(1);
    const login = await caught(api.post("/auth/login", { username: "a", password: "b" }));
    expect(login.detail).toBe("Invalid username or password");
    expect(onUnauthorized).toHaveBeenCalledTimes(1);
  });
});
