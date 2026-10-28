import type { Rule } from "../../api/types";

/** An editable rule: everything the API accepts (id and run info are server-owned). */
export type RuleDraft = Omit<Rule, "id">;

/**
 * The spec's safe "New rule" (no presets): the backend `Rule` defaults exactly,
 * with an empty name for the user to fill in. Disabled until the user enables it.
 */
export function defaultRule(): RuleDraft {
  return {
    name: "",
    enabled: false,
    action: "unmonitor",
    delete_mode: "files",
    add_exclusion: false,
    delete_confirmed: false,
    finding_types: ["wanted_elsewhere"],
    instances: [],
    servers: [],
    min_servers: 1,
    coverage: "full",
    quality: ["better", "same"],
    edition: ["same"],
    match_methods: ["id"],
    wait_scans: 2,
    dry_run_next: false,
  };
}

/** The draft fields of a rule from the API (drops id, last_run, last_affected). */
export function toDraft(rule: Rule): RuleDraft {
  const draft = defaultRule();
  for (const key of Object.keys(draft) as (keyof RuleDraft)[]) {
    (draft as Record<string, unknown>)[key] = rule[key];
  }
  return draft;
}
