import type { RuleAction } from "../../api/types";
import type { RuleDraft } from "./defaults";

export const ACTION_LABELS: Record<RuleAction, string> = {
  unmonitor: "Unmonitor",
  delete: "Delete",
  exclude: "Add exclusion",
  tag: "Tag",
  ignore: "Ignore",
};

/** The verb in "would <action> N items now". */
export function actionVerb(rule: Pick<RuleDraft, "action" | "delete_mode">): string {
  switch (rule.action) {
    case "delete":
      return rule.delete_mode === "remove_item" ? "remove" : "delete files of";
    case "exclude":
      return "add exclusions for";
    default:
      return rule.action;
  }
}

export const DRY_RUN_COPY =
  "Next scan will report what this rule would do, without changing anything.";
