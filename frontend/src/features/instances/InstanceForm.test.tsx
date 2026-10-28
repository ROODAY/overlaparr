import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it, vi } from "vitest";
import { createQueryClient } from "../../api/queryClient";
import { MASK, type Instance } from "../../api/types";
import { server } from "../../test/server";
import { InstanceForm } from "./InstanceForm";

function renderForm(initial?: Instance) {
  const onSaved = vi.fn();
  render(
    <QueryClientProvider client={createQueryClient()}>
      <InstanceForm initial={initial} onSaved={onSaved} />
    </QueryClientProvider>,
  );
  return { onSaved, user: userEvent.setup() };
}

async function fill(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByLabelText("Name"), "Sonarr");
  await user.type(screen.getByLabelText("URL"), "http://sonarr:8989");
  await user.type(screen.getByLabelText("API key"), "secret-key");
}

describe("InstanceForm", () => {
  it("hides the API key until Show is pressed", async () => {
    const { user } = renderForm();
    const key = screen.getByLabelText("API key");
    expect(key).toHaveAttribute("type", "password");
    await user.click(screen.getByRole("button", { name: "Show" }));
    expect(key).toHaveAttribute("type", "text");
    await user.click(screen.getByRole("button", { name: "Hide" }));
    expect(key).toHaveAttribute("type", "password");
  });

  it("clears the test result when a connection field changes", async () => {
    server.use(
      http.post("*/api/v1/instances/test", () =>
        HttpResponse.json({ ok: false, error: "Connection refused" }),
      ),
    );
    const { user } = renderForm();
    await fill(user);
    await user.click(screen.getByRole("button", { name: "Test" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Connection refused");
    await user.type(screen.getByLabelText("URL"), "/");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("masks the key after saving and then updates the saved instance", async () => {
    const posts: unknown[] = [];
    const puts: unknown[] = [];
    let tested: unknown = null;
    server.use(
      http.post("*/api/v1/instances", async ({ request }) => {
        const body = (await request.json()) as Record<string, unknown>;
        posts.push(body);
        return HttpResponse.json({ id: "i1", ...body, api_key: MASK });
      }),
      http.put("*/api/v1/instances/i1", async ({ request }) => {
        const body = (await request.json()) as Record<string, unknown>;
        puts.push(body);
        return HttpResponse.json({ id: "i1", ...body, api_key: MASK });
      }),
      http.post("*/api/v1/instances/test", async ({ request }) => {
        tested = await request.json();
        return HttpResponse.json({ ok: true, version: "4.0" });
      }),
      // Saving a new instance makes it existing, so the form loads its quality profiles.
      http.get("*/api/v1/instances/i1/profiles", () => HttpResponse.json([])),
    );
    const { user, onSaved } = renderForm();
    await fill(user);
    await user.click(screen.getByRole("button", { name: "Save" }));
    await vi.waitFor(() => expect(onSaved).toHaveBeenCalledTimes(1));
    expect(screen.getByLabelText("API key")).toHaveValue(MASK);

    await user.click(screen.getByRole("button", { name: "Test" }));
    await screen.findByText(/Connected/);
    expect(tested).toMatchObject({ api_key: MASK, id: "i1" });

    await user.click(screen.getByRole("button", { name: "Save" }));
    await vi.waitFor(() => expect(onSaved).toHaveBeenCalledTimes(2));
    expect(posts).toHaveLength(1);
    expect(puts).toEqual([expect.objectContaining({ api_key: MASK, name: "Sonarr" })]);
  });
});
