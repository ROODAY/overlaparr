import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useId, useState, type ReactNode } from "react";
import { api, ApiError } from "../api/client";
import { queryKeys } from "../api/queryClient";
import { MASK, type Instance } from "../api/types";
import { Badge } from "../components/Badge";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { Switch } from "../components/Switch";
import { useToast } from "../components/useToast";
import { InstanceForm } from "../features/instances/InstanceForm";

const SMALL_BUTTON = "rounded px-2 py-1 text-slate-300 hover:bg-slate-700 disabled:opacity-50";

function message(error: unknown): string {
  return error instanceof ApiError ? error.detail : "could not reach Overlaparr";
}

const TYPE_LABEL = { sonarr: "Sonarr", radarr: "Radarr" } as const;

export function Instances() {
  const queryClient = useQueryClient();
  const toast = useToast();
  const instances = useQuery({
    queryKey: queryKeys.instances,
    queryFn: () => api.get<Instance[]>("/instances"),
  });
  // "new" opens the form empty; an instance opens it for editing.
  const [editing, setEditing] = useState<Instance | "new" | null>(null);
  const [deleting, setDeleting] = useState<Instance | null>(null);

  const setEnabled = useMutation({
    mutationFn: (instance: Instance) =>
      api.put<Instance>(`/instances/${instance.id}`, {
        name: instance.name,
        type: instance.type,
        url: instance.url,
        api_key: MASK,
        enabled: !instance.enabled,
        loss_handling: instance.loss_handling,
      }),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: queryKeys.instances }),
    onError: (e) => toast(`Could not update the instance: ${message(e)}`, "error"),
  });

  const remove = useMutation({
    mutationFn: (id: string) => api.del(`/instances/${id}`),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.instances });
      void queryClient.invalidateQueries({ queryKey: queryKeys.setupStatus });
      toast("Instance deleted");
      setDeleting(null);
    },
    onError: (e) => {
      toast(`Could not delete the instance: ${message(e)}`, "error");
      setDeleting(null);
    },
  });

  return (
    <div>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold text-slate-100">Instances</h1>
          <p className="mt-1 text-sm text-slate-400">
            Sonarr and Radarr instances whose libraries Overlaparr compares with the Plex servers.
          </p>
        </div>
        <button
          type="button"
          onClick={() => setEditing("new")}
          className="rounded bg-accent px-4 py-2 text-sm font-medium text-white hover:bg-accent-hover"
        >
          Add instance
        </button>
      </div>

      {instances.isPending ? (
        <p className="mt-6 text-slate-400">Loading…</p>
      ) : instances.isError ? (
        <div className="mt-6">
          <p role="alert" className="text-red-300">
            {instances.error instanceof ApiError
              ? instances.error.detail
              : "Could not load instances"}
          </p>
          <button
            type="button"
            onClick={() => void instances.refetch()}
            className="mt-2 rounded border border-slate-600 px-3 py-1 text-sm text-slate-200 hover:bg-slate-700"
          >
            Retry
          </button>
        </div>
      ) : instances.data.length === 0 ? (
        <p className="mt-6 rounded-lg border border-slate-700 bg-slate-800 p-4 text-slate-300">
          No instances yet. Add one to compare its library with the other Plex servers.
        </p>
      ) : (
        <div className="mt-4 overflow-x-auto rounded-lg border border-slate-700">
          <table className="w-full text-sm">
            <thead className="bg-slate-800 text-left text-xs uppercase tracking-wide text-slate-400">
              <tr>
                <th className="px-3 py-2">Instance</th>
                <th className="px-3 py-2">Type</th>
                <th className="px-3 py-2">URL</th>
                <th className="px-3 py-2">Enabled</th>
                <th className="px-3 py-2 text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-700/60">
              {instances.data.map((instance) => (
                <tr key={instance.id} className="align-middle">
                  <td className="px-3 py-2 text-slate-100">{instance.name}</td>
                  <td className="px-3 py-2">
                    <Badge>{TYPE_LABEL[instance.type]}</Badge>
                  </td>
                  <td className="px-3 py-2 text-slate-300">{instance.url}</td>
                  <td className="px-3 py-2">
                    <Switch
                      label={`Enable ${instance.name}`}
                      checked={instance.enabled}
                      disabled={setEnabled.isPending}
                      onChange={() => setEnabled.mutate(instance)}
                    />
                  </td>
                  <td className="px-3 py-2">
                    <div className="flex items-center justify-end gap-1">
                      <button
                        type="button"
                        onClick={() => setEditing(instance)}
                        className={SMALL_BUTTON}
                      >
                        Edit
                      </button>
                      <button
                        type="button"
                        onClick={() => setDeleting(instance)}
                        className={SMALL_BUTTON}
                      >
                        Delete
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {editing && (
        <Modal
          title={editing === "new" ? "Add instance" : "Edit instance"}
          onClose={() => setEditing(null)}
        >
          <InstanceForm
            key={editing === "new" ? "new" : editing.id}
            initial={editing === "new" ? undefined : editing}
            onSaved={() => setEditing(null)}
          />
        </Modal>
      )}

      {deleting && (
        <ConfirmDialog
          open
          title={`Delete ${deleting.name}?`}
          body={
            <>
              This removes the instance from Overlaparr. Its findings disappear at the next scan.
            </>
          }
          confirmLabel="Delete instance"
          destructive
          confirmDisabled={remove.isPending}
          onConfirm={() => remove.mutate(deleting.id)}
          onCancel={() => setDeleting(null)}
        />
      )}
    </div>
  );
}

function Modal({
  title,
  onClose,
  children,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
}) {
  const titleId = useId();
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="fixed inset-0 z-40 flex items-start justify-center overflow-y-auto bg-black/60 p-4">
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className="mt-16 w-full max-w-lg rounded-lg border border-slate-700 bg-slate-800 p-5 shadow-xl"
      >
        <div className="mb-3 flex items-center justify-between">
          <h2 id={titleId} className="text-lg font-semibold text-slate-100">
            {title}
          </h2>
          <button
            type="button"
            aria-label="Close"
            onClick={onClose}
            className="rounded px-2 text-xl text-slate-400 hover:bg-slate-700 hover:text-slate-100"
          >
            ×
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}
