import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, ApiError } from "../api/client";
import { queryKeys } from "../api/queryClient";
import type { Dashboard, Instance, Rule, RuleIn, RuleWithRun } from "../api/types";
import { Switch } from "../components/Switch";
import { useToast } from "../components/useToast";
import { defaultRule, toDraft, type RuleDraft } from "../features/rules/defaults";
import { DryRunButton } from "../features/rules/DryRunButton";
import { EnableDialog } from "../features/rules/EnableDialog";
import { RuleForm } from "../features/rules/RuleForm";
import { RulePreview } from "../features/rules/RulePreview";

/** /automation/new or /automation/:id. Loads the rule, then hands it to the form. */
export function RuleEditor() {
  const { id = "new" } = useParams();
  const isNew = id === "new";
  const rules = useQuery({
    queryKey: queryKeys.rules,
    queryFn: () => api.get<RuleWithRun[]>("/rules"),
    enabled: !isNew,
  });

  if (isNew) return <EditorForm key="new" />;
  if (rules.isPending) return <p className="text-slate-400">Loading…</p>;
  if (rules.isError) {
    return (
      <p role="alert" className="text-red-300">
        {rules.error instanceof ApiError ? rules.error.detail : "Could not load rules"}
      </p>
    );
  }
  const rule = rules.data.find((r) => r.id === id);
  if (!rule) {
    return (
      <p className="text-slate-400">
        This rule no longer exists.{" "}
        <Link to="/automation" className="text-accent hover:underline">
          Back to rules
        </Link>
      </p>
    );
  }
  return <EditorForm key={rule.id} original={rule} />;
}

/** Was the saved rule already enabled (and DELETE-confirmed) for this same action? */
function alreadyConfirmed(original: Rule | undefined, draft: RuleDraft): boolean {
  if (!original?.enabled || original.action !== draft.action) return false;
  if (draft.action !== "delete") return true;
  return original.delete_confirmed && original.delete_mode === draft.delete_mode;
}

function EditorForm({ original }: { original?: Rule }) {
  const [draft, setDraft] = useState<RuleDraft>(() =>
    original ? toDraft(original) : defaultRule(),
  );
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const nameId = useId();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const toast = useToast();

  const instances = useQuery({
    queryKey: queryKeys.instances,
    queryFn: () => api.get<Instance[]>("/instances"),
  });
  const dashboard = useQuery({
    queryKey: queryKeys.dashboard,
    queryFn: () => api.get<Dashboard>("/dashboard"),
  });

  const save = useMutation({
    mutationFn: (body: RuleIn) =>
      original ? api.put<Rule>(`/rules/${original.id}`, body) : api.post<Rule>("/rules", body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.rules });
      toast("Rule saved");
      navigate("/automation");
    },
    onError: (e) => setError(e instanceof ApiError ? e.detail : "Could not reach Overlaparr"),
  });

  const patch = (changes: Partial<RuleDraft>) => setDraft((d) => ({ ...d, ...changes }));
  // An enabled delete rule only reaches here once DELETE was typed, now or before.
  const submit = () =>
    save.mutate({ ...draft, delete_confirmed: draft.action === "delete" && draft.enabled });
  const nameMissing = draft.name.trim() === "";

  return (
    <div className="max-w-3xl">
      <h1 className="text-2xl font-semibold text-slate-100">
        {original ? "Edit rule" : "New rule"}
      </h1>
      <p className="mt-1 text-sm text-slate-400">
        Every field is a choice; rules run after each scan in list order.
      </p>
      <form
        className="mt-4"
        onSubmit={(e) => {
          e.preventDefault();
          setError(null);
          if (nameMissing) return;
          if (draft.enabled && !alreadyConfirmed(original, draft)) setConfirming(true);
          else submit();
        }}
      >
        <div className="flex flex-wrap items-center gap-3 border-b border-slate-700/60 pb-3">
          <label htmlFor={nameId} className="w-48 text-sm text-slate-300">
            Name
          </label>
          <input
            id={nameId}
            value={draft.name}
            onChange={(e) => patch({ name: e.target.value })}
            placeholder="e.g. Unmonitor wanted movies friends have"
            className="min-w-0 flex-1 rounded border border-slate-600 bg-slate-900 px-3 py-1.5 text-sm text-slate-100 focus:border-accent focus:outline-none"
          />
          <span className="flex items-center gap-2 text-sm text-slate-300">
            Enabled
            <Switch
              label="Enabled"
              checked={draft.enabled}
              onChange={(enabled) => patch({ enabled })}
            />
          </span>
        </div>
        <RuleForm
          draft={draft}
          onChange={patch}
          instances={instances.data ?? []}
          servers={dashboard.data?.servers ?? []}
        />
        <RulePreview draft={draft} ruleId={original?.id} />
        {error && (
          <p role="alert" className="mt-3 text-sm text-red-300">
            {error}
          </p>
        )}
        <div className="mt-4 flex flex-wrap items-center gap-2">
          {original && (
            <DryRunButton
              rule={{ id: original.id, dry_run_next: draft.dry_run_next }}
              onQueued={() => patch({ dry_run_next: true })}
            />
          )}
          <Link
            to="/automation"
            className="ml-auto rounded px-4 py-2 text-sm text-slate-300 hover:bg-slate-700"
          >
            Cancel
          </Link>
          <button
            type="submit"
            disabled={nameMissing || save.isPending}
            title={nameMissing ? "Give the rule a name" : undefined}
            className="rounded bg-accent px-4 py-2 text-sm font-medium text-white hover:bg-accent-hover disabled:cursor-not-allowed disabled:opacity-50"
          >
            Save rule
          </button>
        </div>
      </form>
      <EnableDialog
        open={confirming}
        action={draft.action}
        ruleName={draft.name}
        confirmLabel="Enable and save"
        onCancel={() => setConfirming(false)}
        onConfirm={() => {
          setConfirming(false);
          submit();
        }}
      />
    </div>
  );
}
