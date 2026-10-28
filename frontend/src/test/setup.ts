import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterAll, afterEach, beforeAll, vi } from "vitest";
import { FakeEventSource } from "./fakeEventSource";
import { server } from "./server";

// jsdom has no EventSource; pages that open the scan stream get a silent fake.
vi.stubGlobal("EventSource", FakeEventSource);

beforeAll(() => server.listen({ onUnhandledFrame: "error" }));
afterEach(() => {
  cleanup();
  server.resetHandlers();
});
afterAll(() => server.close());
