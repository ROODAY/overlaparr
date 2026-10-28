import { describe, expect, it } from "vitest";
import schemaSource from "../../../../backend/overlaparr/config/schema.py?raw";
import { defaultRule } from "./defaults";

/** Field name -> default (null when required) from the backend's `class Rule`. */
function backendRuleDefaults(source: string): Record<string, unknown> {
  const body = source.split("class Rule(_Base):")[1].split("@model_validator")[0];
  const out: Record<string, unknown> = {};
  for (const line of body.split("\n")) {
    const m = /^ {4}(\w+): [^=]+?(?: = (.+))?$/.exec(line.trimEnd());
    if (!m) continue;
    const [, field, raw] = m;
    if (raw === undefined) {
      out[field] = null;
      continue;
    }
    const expr = /^Field\(default=([^,)]+)/.exec(raw)?.[1] ?? raw;
    out[field] = JSON.parse(expr.replace(/\bTrue\b/g, "true").replace(/\bFalse\b/g, "false"));
  }
  return out;
}

describe("defaultRule", () => {
  it("matches the backend Rule defaults exactly", () => {
    const backend = backendRuleDefaults(schemaSource);
    expect(Object.keys(backend).length).toBeGreaterThan(10);
    const { id, name, ...defaults } = backend;
    expect(id).toBeNull();
    expect(name).toBeNull(); // required by the backend; the editor starts it empty
    expect(defaultRule()).toEqual({ name: "", ...defaults });
  });

  it("is the spec's safe, disabled starting point", () => {
    expect(defaultRule()).toMatchObject({
      enabled: false,
      action: "unmonitor",
      finding_types: ["wanted_elsewhere"],
      instances: [],
      servers: [],
      min_servers: 1,
      coverage: "full",
      quality: ["better", "same"],
      edition: ["same"],
      match_methods: ["id"],
      wait_scans: 2,
    });
  });

  it("returns a fresh object each time", () => {
    const a = defaultRule();
    a.quality.push("worse");
    expect(defaultRule().quality).toEqual(["better", "same"]);
  });
});
