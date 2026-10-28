import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useId, useState, type FormEvent, type ReactNode } from "react";
import { api, ApiError } from "../../api/client";
import { queryKeys } from "../../api/queryClient";
import { SecretInput } from "../../components/SecretInput";
import {
  MASK,
  type Instance,
  type InstanceIn,
  type InstanceTest,
  type InstanceTestResult,
  type InstanceType,
  type LossHandling,
} from "../../api/types";

const INPUT =
  "mt-1 w-full rounded border border-slate-600 bg-slate-900 px-3 py-2 text-slate-100 focus:border-accent focus:outline-none";

const LOSS_OPTIONS: { key: "remonitor" | "remove_exclusion" | "search"; label: string }[] = [
  { key: "remonitor", label: "Re-monitor items Overlaparr unmonitored" },
  { key: "remove_exclusion", label: "Remove exclusions Overlaparr added" },
  { key: "search", label: "Search for re-monitored items" },
];

const DEFAULT_LOSS: LossHandling = {
  remonitor: false,
  remove_exclusion: false,
  quality_profile_id: null,
  search: false,
};

export interface InstanceFormProps {
  /** Existing instance to edit (its api_key comes back masked); omit to add a new one. */
  initial?: Instance;
  onSaved: (instance: Instance) => void;
  /** Extra buttons rendered next to Test/Save (e.g. Cancel or "Skip for now"). */
  actions?: ReactNode;
}

function message(error: unknown): string {
  return error instanceof ApiError ? error.detail : "Could not reach Overlaparr";
}

/** Add or edit a Sonarr/Radarr instance, with a connection test. */
export function InstanceForm({ initial, onSaved, actions }: InstanceFormProps) {
  const queryClient = useQueryClient();
  const ids = { name: useId(), type: useId(), url: useId(), key: useId() };
  const [name, setName] = useState(initial?.name ?? "");
  const [type, setType] = useState<InstanceType>(initial?.type ?? "sonarr");
  const [url, setUrl] = useState(initial?.url ?? "");
  const [apiKey, setApiKey] = useState(initial?.api_key ?? "");
  const [enabled, setEnabled] = useState(initial?.enabled ?? true);
  const [loss, setLoss] = useState<LossHandling>(initial?.loss_handling ?? DEFAULT_LOSS);
  // Set once saved, so later saves update this instance and a masked key tests with the stored one.
  const [savedId, setSavedId] = useState<string | undefined>(initial?.id);

  const test = useMutation({
    mutationFn: (body: InstanceTest) => api.post<InstanceTestResult>("/instances/test", body),
  });
  const save = useMutation({
    mutationFn: (body: InstanceIn) =>
      savedId
        ? api.put<Instance>(`/instances/${savedId}`, body)
        : api.post<Instance>("/instances", body),
    onSuccess: (saved) => {
      setSavedId(saved.id);
      setApiKey(MASK); // the key is stored now; don't keep it in the page
      void queryClient.invalidateQueries({ queryKey: queryKeys.instances });
      void queryClient.invalidateQueries({ queryKey: queryKeys.setupStatus });
      onSaved(saved);
    },
  });

  const runTest = () => {
    const body: InstanceTest = { type, url: url.trim(), api_key: apiKey };
    // A masked key on an existing instance: the backend tests with the stored key.
    if (savedId && apiKey === MASK) body.id = savedId;
    test.mutate(body);
  };

  const submit = (e: FormEvent) => {
    e.preventDefault();
    save.mutate({
      name: name.trim(),
      type,
      url: url.trim(),
      api_key: apiKey,
      enabled,
      loss_handling: loss,
    });
  };

  /** Connection fields changed: the last test result no longer applies. */
  const changed = <T,>(set: (value: T) => void) => (value: T) => {
    test.reset();
    set(value);
  };

  let testResult: ReactNode = null;
  if (test.isError) {
    testResult = <Alert>{message(test.error)}</Alert>;
  } else if (test.data && !test.data.ok) {
    testResult = <Alert>{test.data.error}</Alert>;
  } else if (test.data?.ok) {
    testResult = (
      <p role="status" className="text-sm text-green-300">
        Connected{test.data.version ? ` (version ${test.data.version})` : ""}
      </p>
    );
  }

  return (
    <form onSubmit={submit} className="space-y-3">
      <div>
        <label htmlFor={ids.name} className="block text-sm text-slate-300">
          Name
        </label>
        <input
          id={ids.name}
          className={INPUT}
          required
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
      </div>
      <div>
        <label htmlFor={ids.type} className="block text-sm text-slate-300">
          Type
        </label>
        <select
          id={ids.type}
          className={INPUT}
          value={type}
          onChange={(e) => changed(setType)(e.target.value as InstanceType)}
        >
          <option value="sonarr">Sonarr</option>
          <option value="radarr">Radarr</option>
        </select>
      </div>
      <div>
        <label htmlFor={ids.url} className="block text-sm text-slate-300">
          URL
        </label>
        <input
          id={ids.url}
          className={INPUT}
          type="url"
          required
          placeholder={type === "sonarr" ? "http://sonarr:8989" : "http://radarr:7878"}
          value={url}
          onChange={(e) => changed(setUrl)(e.target.value)}
        />
      </div>
      <div>
        <label htmlFor={ids.key} className="block text-sm text-slate-300">
          API key
        </label>
        <SecretInput
          id={ids.key}
          className={INPUT}
          required
          value={apiKey}
          onFocus={(e) => apiKey === MASK && e.target.select()}
          onChange={(e) => changed(setApiKey)(e.target.value)}
        />
      </div>
      <label className="flex items-center gap-2 text-sm text-slate-300">
        <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
        Enabled
      </label>
      <fieldset className="rounded border border-slate-700 p-3">
        <legend className="px-1 text-sm text-slate-300">
          When an item is no longer available elsewhere
        </legend>
        {LOSS_OPTIONS.map((opt) => (
          <label key={opt.key} className="mt-1 flex items-center gap-2 text-sm text-slate-300">
            <input
              type="checkbox"
              checked={loss[opt.key]}
              onChange={(e) => setLoss({ ...loss, [opt.key]: e.target.checked })}
            />
            {opt.label}
          </label>
        ))}
      </fieldset>
      {testResult}
      {save.isError && <Alert>{message(save.error)}</Alert>}
      <div className="flex flex-wrap gap-2 pt-1">
        <button
          type="button"
          onClick={runTest}
          disabled={test.isPending || !url.trim() || !apiKey}
          className="rounded border border-slate-600 px-4 py-2 text-sm text-slate-200 hover:bg-slate-700 disabled:opacity-50"
        >
          Test
        </button>
        <button
          type="submit"
          disabled={save.isPending}
          className="rounded bg-accent px-4 py-2 text-sm font-medium text-white hover:bg-accent-hover disabled:opacity-50"
        >
          Save
        </button>
        {actions}
      </div>
    </form>
  );
}

function Alert({ children }: { children: ReactNode }) {
  return (
    <p
      role="alert"
      className="rounded border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-300"
    >
      {children}
    </p>
  );
}
