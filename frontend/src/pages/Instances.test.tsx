import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { beforeEach, describe, expect, it } from "vitest";
import {
  MASK,
  type Instance,
  type InstanceIn,
  type QualityProfile,
} from "../api/types";
import { instanceFixture } from "../test/fixtures";
import { renderWithProviders } from "../test/render";
import { server } from "../test/server";
import { Instances } from "./Instances";

let instances: Instance[];
let puts: { id: string; body: InstanceIn }[];
let posts: InstanceIn[];
let deletes: string[];
let profiles: QualityProfile[];
let tests: unknown[];

beforeEach(() => {
  instances = [instanceFixture()];
  puts = [];
  posts = [];
  deletes = [];
  tests = [];
  profiles = [
    { id: 1, name: "HD-1080p" },
    { id: 2, name: "Ultra-HD" },
  ];
  server.use(
    http.get("*/api/v1/instances", () => HttpResponse.json(instances)),
    http.get("*/api/v1/instances/:id/profiles", () => HttpResponse.json(profiles)),
    http.post("*/api/v1/instances", async ({ request }) => {
      const body = (await request.json()) as InstanceIn;
      posts.push(body);
      const created: Instance = {
        id: "i-new",
        name: body.name,
        type: body.type,
        url: body.url,
        api_key: MASK,
        enabled: body.enabled ?? true,
        loss_handling: {
          remonitor: false,
          remove_exclusion: false,
          quality_profile_id: null,
          search: false,
          ...body.loss_handling,
        },
      };
      instances = [...instances, created];
      return HttpResponse.json(created);
    }),
    http.put("*/api/v1/instances/:id", async ({ params, request }) => {
      const body = (await request.json()) as InstanceIn;
      const id = String(params.id);
      puts.push({ id, body });
      instances = instances.map((i) =>
        i.id === id
          ? {
              ...i,
              enabled: body.enabled ?? i.enabled,
              name: body.name,
              type: body.type,
              url: body.url,
              loss_handling: { ...i.loss_handling, ...body.loss_handling },
            }
          : i,
      );
      return HttpResponse.json(instances.find((i) => i.id === id));
    }),
    http.delete("*/api/v1/instances/:id", ({ params }) => {
      const id = String(params.id);
      deletes.push(id);
      instances = instances.filter((i) => i.id !== id);
      return HttpResponse.json({ ok: true });
    }),
    http.post("*/api/v1/instances/test", async ({ request }) => {
      tests.push(await request.json());
      return HttpResponse.json({ ok: true, version: "4.2.1" });
    }),
  );
});

function row(name: RegExp) {
  return within(screen.getByRole("row", { name }));
}

async function openEdit(user: ReturnType<typeof userEvent.setup>) {
  await screen.findByRole("row", { name: /Sonarr/ });
  await user.click(row(/Sonarr/).getByRole("button", { name: "Edit" }));
  return screen.findByRole("dialog");
}

describe("Instances", () => {
  it("lists instances with type, url and enabled switch", async () => {
    renderWithProviders(<Instances />, { route: "/instances" });
    expect(await screen.findByRole("heading", { name: "Instances" })).toBeInTheDocument();
    await screen.findByRole("row", { name: /Sonarr/ });
    const sonarr = row(/Sonarr/);
    expect(sonarr.getByText("http://sonarr:8989")).toBeInTheDocument();
    expect(sonarr.getByRole("switch", { name: "Enable Sonarr" })).toHaveAttribute(
      "aria-checked",
      "true",
    );
  });

  it("instance test button shows version", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Instances />, { route: "/instances" });
    await screen.findByRole("row", { name: /Sonarr/ });
    await user.click(screen.getByRole("button", { name: "Add instance" }));
    const dialog = await screen.findByRole("dialog");

    await user.type(within(dialog).getByLabelText("Name"), "Sonarr 2");
    await user.type(within(dialog).getByLabelText("URL"), "http://sonarr2:8989");
    await user.type(within(dialog).getByLabelText("API key"), "secret");
    await user.click(within(dialog).getByRole("button", { name: "Test" }));

    expect(await within(dialog).findByText("Connected (version 4.2.1)")).toBeInTheDocument();
    expect(tests).toEqual([
      { type: "sonarr", url: "http://sonarr2:8989", api_key: "secret" },
    ]);
  });

  it("masked key not overwritten on save", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Instances />, { route: "/instances" });
    const dialog = await openEdit(user);

    expect(within(dialog).getByLabelText("Name")).toHaveValue("Sonarr");
    await user.clear(within(dialog).getByLabelText("Name"));
    await user.type(within(dialog).getByLabelText("Name"), "Sonarr renamed");
    await user.click(within(dialog).getByRole("button", { name: "Save" }));

    await waitFor(() => expect(puts).toHaveLength(1));
    expect(puts[0]).toMatchObject({ id: "i-1", body: { api_key: MASK, name: "Sonarr renamed" } });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("adds an instance with POST", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Instances />, { route: "/instances" });
    await screen.findByRole("row", { name: /Sonarr/ });
    await user.click(screen.getByRole("button", { name: "Add instance" }));
    const dialog = await screen.findByRole("dialog");

    await user.type(within(dialog).getByLabelText("Name"), "Radarr");
    await user.selectOptions(within(dialog).getByLabelText("Type"), "radarr");
    await user.type(within(dialog).getByLabelText("URL"), "http://radarr:7878");
    await user.type(within(dialog).getByLabelText("API key"), "secret");
    await user.click(within(dialog).getByRole("button", { name: "Save" }));

    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0]).toMatchObject({
      name: "Radarr",
      type: "radarr",
      url: "http://radarr:7878",
      api_key: "secret",
      enabled: true,
    });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(await screen.findByRole("row", { name: /Radarr/ })).toBeInTheDocument();
  });

  it("loads quality profiles for an existing instance and saves the chosen id", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Instances />, { route: "/instances" });
    const dialog = await openEdit(user);

    const select = await within(dialog).findByLabelText("Quality profile");
    await waitFor(() =>
      expect(within(select).getByRole("option", { name: "Ultra-HD" })).toBeInTheDocument(),
    );
    expect(within(select).getByRole("option", { name: "Leave unchanged" })).toHaveAttribute(
      "value",
      "",
    );
    await user.selectOptions(select, "2");
    await user.click(within(dialog).getByRole("button", { name: "Save" }));

    await waitFor(() => expect(puts).toHaveLength(1));
    expect(puts[0].body.loss_handling).toMatchObject({ quality_profile_id: 2 });
  });

  it("shows the error line when quality profiles fail to load", async () => {
    server.use(
      http.get("*/api/v1/instances/:id/profiles", () =>
        HttpResponse.json({ detail: "Could not reach Sonarr" }, { status: 502 }),
      ),
    );
    const user = userEvent.setup();
    renderWithProviders(<Instances />, { route: "/instances" });
    const dialog = await openEdit(user);

    expect(
      await within(dialog).findByText(
        "Could not load quality profiles: Could not reach Sonarr",
        {},
        { timeout: 5000 },
      ),
    ).toBeInTheDocument();
  });

  it("toggles an instance with a full PUT body and the masked key", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Instances />, { route: "/instances" });
    await screen.findByRole("row", { name: /Sonarr/ });

    await user.click(row(/Sonarr/).getByRole("switch", { name: "Enable Sonarr" }));
    await waitFor(() => expect(puts).toHaveLength(1));
    expect(puts[0]).toEqual({
      id: "i-1",
      body: {
        name: "Sonarr",
        type: "sonarr",
        url: "http://sonarr:8989",
        api_key: MASK,
        enabled: false,
        loss_handling: instanceFixture().loss_handling,
      },
    });
    await waitFor(() =>
      expect(row(/Sonarr/).getByRole("switch", { name: "Enable Sonarr" })).toHaveAttribute(
        "aria-checked",
        "false",
      ),
    );
  });

  it("deletes an instance after confirmation", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Instances />, { route: "/instances" });
    await screen.findByRole("row", { name: /Sonarr/ });

    await user.click(row(/Sonarr/).getByRole("button", { name: "Delete" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/findings disappear at the next scan/i)).toBeInTheDocument();
    expect(deletes).toEqual([]);

    await user.click(within(dialog).getByRole("button", { name: "Delete instance" }));
    await waitFor(() => expect(deletes).toEqual(["i-1"]));
    expect(await screen.findByText("Instance deleted")).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByRole("row", { name: /Sonarr/ })).not.toBeInTheDocument());
  });
});
