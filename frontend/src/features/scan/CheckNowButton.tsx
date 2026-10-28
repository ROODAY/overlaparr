import { useMutation } from "@tanstack/react-query";
import { api, ApiError } from "../../api/client";
import type { ScanPhase, ScanStarted } from "../../api/types";
import { useToast } from "../../components/useToast";
import { useScanEvents, type ScanEventsOptions } from "./useScanEvents";

const PHASE_LABELS: Record<ScanPhase, string> = {
  servers: "Discovering servers",
  indexing: "Indexing servers",
  instances: "Reading instances",
  matching: "Matching",
  reconcile: "Updating findings",
  hooks: "Running automation",
  done: "Finishing",
};

/** e.g. "Indexing servers: Friend (1/3)". */
function phaseText(
  phase: ScanPhase | null,
  detail: string | null,
  done: number,
  total: number,
): string {
  let text = phase ? PHASE_LABELS[phase] : "Starting";
  if (detail) text += `: ${detail}`;
  if (total > 0) text += ` (${done}/${total})`;
  return text;
}

/** Starts a manual scan; disabled with live progress while any scan runs. */
export function CheckNowButton({ scanOptions }: { scanOptions?: ScanEventsOptions }) {
  const scan = useScanEvents(scanOptions);
  const toast = useToast();
  const start = useMutation({
    mutationFn: () => api.post<ScanStarted>("/scan"),
    onError: (err) => {
      if (err instanceof ApiError && err.status === 409) toast("Scan already running");
      else toast(err instanceof ApiError ? err.detail : "Could not start the scan", "error");
    },
    // Don't wait for the stream to show the new (or already running) scan.
    onSettled: () => scan.refresh(),
  });

  const busy = scan.running || start.isPending;
  return (
    <div className="flex flex-wrap items-center gap-3">
      {scan.running && (
        <span className="text-sm text-slate-400">
          {phaseText(scan.phase, scan.detail, scan.done, scan.total)}
        </span>
      )}
      <button
        type="button"
        disabled={busy}
        onClick={() => start.mutate()}
        className="rounded bg-accent px-3 py-1.5 text-sm font-medium text-white hover:bg-accent-hover disabled:cursor-not-allowed disabled:opacity-60"
      >
        {scan.running ? "Scanning…" : "Check now"}
      </button>
    </div>
  );
}
