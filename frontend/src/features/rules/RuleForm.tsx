import type {
  Coverage,
  DashboardServer,
  DeleteMode,
  EditionCmp,
  FindingType,
  Instance,
  Quality,
  RuleAction,
} from "../../api/types";
import { AnyOrSomeChips, FieldRow, MultiChips, SingleChips, type ChipOption } from "./Chips";
import type { RuleDraft } from "./defaults";
import { ACTION_LABELS } from "./labels";

const ACTIONS = Object.entries(ACTION_LABELS) as ChipOption<RuleAction>[];
const DELETE_MODES: ChipOption<DeleteMode>[] = [
  ["files", "Delete files"],
  ["remove_item", "Remove from Radarr"],
];
const TYPES: ChipOption<FindingType>[] = [
  ["wanted_elsewhere", "Wanted elsewhere"],
  ["on_disk", "On disk"],
];
const COVERAGE: ChipOption<Coverage>[] = [
  ["full", "Full only"],
  ["partial", "Partial: act on covered episodes"],
];
const QUALITY: ChipOption<Quality>[] = [
  ["better", "Better"],
  ["same", "Same"],
  ["worse", "Worse"],
  ["unknown", "Unknown"],
];
const EDITION: ChipOption<EditionCmp>[] = [
  ["same", "Same"],
  ["unknown", "Unknown"],
  ["different", "Different"],
];
type Matching = "id" | "number";
const MATCHING: ChipOption<Matching>[] = [
  ["id", "By ID only"],
  ["number", "Also by number"],
];

const inputClass =
  "w-20 rounded border border-slate-600 bg-slate-900 px-2 py-1 text-sm text-slate-100 focus:border-accent focus:outline-none";

function count(value: string): number {
  const n = Number.parseInt(value, 10);
  return Number.isNaN(n) ? 0 : n;
}

export function RuleForm({
  draft,
  onChange,
  instances,
  servers,
}: {
  draft: RuleDraft;
  onChange: (patch: Partial<RuleDraft>) => void;
  instances: Instance[];
  servers: DashboardServer[];
}) {
  const sonarr = instances.filter((i) => i.type === "sonarr").map((i) => i.id);
  const coversTv =
    draft.instances.length === 0
      ? sonarr.length > 0
      : draft.instances.some((id) => sonarr.includes(id));

  return (
    <div>
      <FieldRow
        label="Action"
        hint="For TV, rules act on episodes only. Series stay monitored, so new episodes keep downloading."
      >
        <SingleChips
          label="Action"
          options={ACTIONS}
          value={draft.action}
          onChange={(action) => onChange({ action })}
        />
      </FieldRow>
      {draft.action === "delete" && (
        <FieldRow
          label="Delete mode"
          hint="Removing applies to movies only. TV findings always act on episodes: their files are deleted and the episodes unmonitored; series are never removed."
        >
          <SingleChips
            label="Delete mode"
            options={DELETE_MODES}
            value={draft.delete_mode}
            onChange={(delete_mode) =>
              onChange({ delete_mode, add_exclusion: delete_mode === "remove_item" && draft.add_exclusion })
            }
          />
          {draft.delete_mode === "remove_item" && (
            <label className="flex items-center gap-2 text-sm text-slate-300">
              <input
                type="checkbox"
                checked={draft.add_exclusion}
                onChange={(e) => onChange({ add_exclusion: e.target.checked })}
              />
              Also add an import list exclusion
            </label>
          )}
        </FieldRow>
      )}
      <FieldRow label="Finding type">
        <MultiChips
          label="Finding type"
          options={TYPES}
          value={draft.finding_types}
          onChange={(finding_types) => onChange({ finding_types })}
        />
      </FieldRow>
      <FieldRow label="Instances">
        <AnyOrSomeChips
          label="Instances"
          allLabel="All instances"
          options={instances.map((i) => [i.id, i.name])}
          value={draft.instances}
          onChange={(ids) => onChange({ instances: ids })}
        />
      </FieldRow>
      <FieldRow label="Servers">
        <AnyOrSomeChips
          label="Servers"
          allLabel="Any server"
          options={servers.map((s) => [s.id, s.label])}
          value={draft.servers}
          onChange={(ids) => onChange({ servers: ids })}
        />
        <label className="flex items-center gap-2 text-sm text-slate-300">
          Minimum servers
          <input
            type="number"
            min={1}
            className={inputClass}
            value={draft.min_servers || ""}
            onChange={(e) => onChange({ min_servers: count(e.target.value) })}
          />
        </label>
      </FieldRow>
      <FieldRow label="Coverage (TV)">
        <SingleChips
          label="Coverage (TV)"
          options={COVERAGE}
          value={draft.coverage}
          onChange={(coverage) => onChange({ coverage })}
        />
      </FieldRow>
      <FieldRow label="Their quality vs yours">
        <MultiChips
          label="Their quality vs yours"
          options={QUALITY}
          value={draft.quality}
          onChange={(quality) => onChange({ quality })}
        />
      </FieldRow>
      <FieldRow
        label="Edition (movies only)"
        hint={coversTv ? "TV findings match regardless of edition." : undefined}
      >
        <MultiChips
          label="Edition (movies only)"
          options={EDITION}
          value={draft.edition}
          onChange={(edition) => onChange({ edition })}
        />
      </FieldRow>
      <FieldRow label="Episode matching (TV)">
        <SingleChips
          label="Episode matching (TV)"
          options={MATCHING}
          value={draft.match_methods.includes("number") ? "number" : "id"}
          onChange={(m) => onChange({ match_methods: m === "number" ? ["id", "number"] : ["id"] })}
        />
      </FieldRow>
      <FieldRow
        label="Wait before acting"
        hint="Avoids acting on something a friend adds and then removes."
      >
        <label className="flex items-center gap-2 text-sm text-slate-300">
          Seen on
          <input
            type="number"
            min={1}
            aria-label="Wait (consecutive scans)"
            className={inputClass}
            value={draft.wait_scans || ""}
            onChange={(e) => onChange({ wait_scans: count(e.target.value) })}
          />
          consecutive scans
        </label>
      </FieldRow>
    </div>
  );
}
