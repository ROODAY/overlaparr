import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import { Navigate, useNavigate } from "react-router-dom";
import { api, ApiError } from "../api/client";
import { queryKeys } from "../api/queryClient";
import type {
  Credentials,
  HomeResult,
  Instance,
  LoginResult,
  Me,
  PlexConnected,
  PlexPin,
  ScanStatus,
  ServerRow,
  SetupStatus,
} from "../api/types";
import { SecretInput } from "../components/SecretInput";
import { InstanceForm } from "../features/instances/InstanceForm";

const INPUT =
  "mt-1 w-full rounded border border-slate-600 bg-slate-900 px-3 py-2 text-slate-100 focus:border-accent focus:outline-none";
const PRIMARY =
  "rounded bg-accent px-4 py-2 text-sm font-medium text-white hover:bg-accent-hover disabled:opacity-50";
const SECONDARY =
  "rounded border border-slate-600 px-4 py-2 text-sm text-slate-200 hover:bg-slate-700 disabled:opacity-50";

const STEPS = [
  { id: "user", label: "Account" },
  { id: "plex", label: "Plex" },
  { id: "home", label: "Home server" },
  { id: "instance", label: "Instance" },
  { id: "finish", label: "Finish" },
] as const;
type Step = (typeof STEPS)[number]["id"];

const PLEX_POLL_MS = 2000;
const SCAN_POLL_MS = 3000;

function message(error: unknown): string {
  return error instanceof ApiError ? error.detail : "Could not reach Overlaparr";
}

function isComplete(s: SetupStatus): boolean {
  return !s.needs_user && s.plex_connected && s.home_server_set && s.instances_count > 0;
}

function firstIncomplete(s: SetupStatus): Step {
  if (s.needs_user) return "user";
  if (!s.plex_connected) return "plex";
  if (!s.home_server_set) return "home";
  return "instance";
}

export interface SetupProps {
  /** How often the Plex PIN is polled (tests pass a short interval). */
  plexPollMs?: number;
  /** How often scan status is polled while waiting to start the first full scan. */
  scanPollMs?: number;
}

type Intervals = Required<SetupProps>;

export function Setup({ plexPollMs = PLEX_POLL_MS, scanPollMs = SCAN_POLL_MS }: SetupProps) {
  const status = useQuery({
    queryKey: queryKeys.setupStatus,
    queryFn: () => api.get<SetupStatus>("/setup/status"),
  });
  if (status.isPending) return <Shell>Loading…</Shell>;
  if (status.isError) return <Shell>{message(status.error)}</Shell>;
  return <SetupGate status={status.data} intervals={{ plexPollMs, scanPollMs }} />;
}

/** Decides once, from the status on arrival, whether to run the wizard and where to start;
 * later status refreshes (as steps complete) do not move the user around. */
function SetupGate({ status, intervals }: { status: SetupStatus; intervals: Intervals }) {
  const [initial] = useState(status);
  const needsLogin = !initial.needs_user && !isComplete(initial);
  const me = useQuery({
    queryKey: queryKeys.me,
    queryFn: () => api.get<Me>("/auth/me"),
    enabled: needsLogin,
  });

  if (isComplete(initial)) return <Navigate to="/" replace />;
  if (initial.needs_user) return <Wizard start="user" intervals={intervals} />;
  if (me.isPending) return <Shell>Loading…</Shell>;
  if (me.isError) {
    if (me.error instanceof ApiError && me.error.status === 401) {
      return <Navigate to="/login" replace state={{ from: "/setup" }} />;
    }
    return <Shell>{message(me.error)}</Shell>;
  }
  return <Wizard start={firstIncomplete(initial)} intervals={intervals} />;
}

function Shell({ children }: { children: ReactNode }) {
  return (
    <main className="flex min-h-screen items-start justify-center p-4 sm:items-center">
      <div className="w-full max-w-lg rounded-lg border border-slate-700 bg-slate-800 p-6 shadow-xl">
        <p className="text-sm font-semibold tracking-wide text-accent">Overlaparr</p>
        <h1 className="mt-1 text-xl font-semibold text-slate-100">Setup</h1>
        <div className="mt-4 text-slate-300">{children}</div>
      </div>
    </main>
  );
}

function Wizard({ start, intervals }: { start: Step; intervals: Intervals }) {
  const [step, setStep] = useState<Step>(start);
  const [notice, setNotice] = useState<string | null>(null);
  const [instanceAdded, setInstanceAdded] = useState(false);
  const current = STEPS.findIndex((s) => s.id === step);

  return (
    <Shell>
      <ol className="mb-5 flex flex-wrap gap-x-3 gap-y-1 text-xs" aria-label="Setup steps">
        {STEPS.map((s, i) => (
          <li
            key={s.id}
            aria-current={i === current ? "step" : undefined}
            className={
              i === current
                ? "font-semibold text-accent"
                : i < current
                  ? "text-slate-400"
                  : "text-slate-500"
            }
          >
            {i + 1}. {s.label}
          </li>
        ))}
      </ol>
      {notice && (
        <p className="mb-4 rounded border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-sm text-amber-200">
          {notice}
        </p>
      )}
      {step === "user" && (
        <UserStep
          onDone={(warning) => {
            setNotice(warning);
            setStep("plex");
          }}
        />
      )}
      {step === "plex" && (
        <PlexStep pollMs={intervals.plexPollMs} onDone={() => setStep("home")} />
      )}
      {step === "home" && <HomeStep onDone={() => setStep("instance")} />}
      {step === "instance" && (
        <InstanceStep
          onDone={(added) => {
            setInstanceAdded(added);
            setStep("finish");
          }}
        />
      )}
      {step === "finish" && (
        <FinishStep instanceAdded={instanceAdded} pollMs={intervals.scanPollMs} />
      )}
    </Shell>
  );
}

function StepHeading({ children }: { children: ReactNode }) {
  return <h2 className="text-lg font-semibold text-slate-100">{children}</h2>;
}

function Alert({ children }: { children: ReactNode }) {
  return (
    <p
      role="alert"
      className="mt-3 rounded border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-300"
    >
      {children}
    </p>
  );
}

// ---------- Step 1: account ----------

function UserStep({ onDone }: { onDone: (warning: string | null) => void }) {
  const queryClient = useQueryClient();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [localBypass, setLocalBypass] = useState(false);
  // Both requests run inside the mutation, so the form stays disabled until both finish.
  const create = useMutation({
    mutationFn: async ({
      localBypass: bypass,
      ...credentials
    }: Credentials & { localBypass: boolean }): Promise<string | null> => {
      await api.post<LoginResult>("/setup/user", credentials);
      if (!bypass) return null;
      try {
        await api.put("/settings", { auth: { local_bypass: true } });
        return null;
      } catch (err) {
        return `Your account was created, but login could not be disabled for the local network: ${message(err)}. You can change this later in Settings.`;
      }
    },
    onSuccess: (warning) => {
      // The new user is signed in now; drop the stale "needs user" and "signed out" state.
      queryClient.removeQueries({ queryKey: queryKeys.me });
      void queryClient.invalidateQueries({ queryKey: queryKeys.setupStatus });
      onDone(warning);
    },
  });

  const submit = (e: FormEvent) => {
    e.preventDefault();
    create.mutate({ username, password, localBypass });
  };

  return (
    <form onSubmit={submit}>
      <StepHeading>Create your account</StepHeading>
      <p className="mt-1 text-sm text-slate-400">This is the login for Overlaparr itself.</p>
      {create.isError && <Alert>{message(create.error)}</Alert>}
      <label className="mt-4 block text-sm text-slate-300">
        Username
        <input
          className={INPUT}
          autoComplete="username"
          autoFocus
          required
          value={username}
          onChange={(e) => setUsername(e.target.value)}
        />
      </label>
      <label className="mt-3 block text-sm text-slate-300">
        Password
        <input
          className={INPUT}
          type="password"
          autoComplete="new-password"
          required
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
      </label>
      <label className="mt-4 flex items-center gap-2 text-sm text-slate-300">
        <input
          type="checkbox"
          checked={localBypass}
          onChange={(e) => setLocalBypass(e.target.checked)}
        />
        Disable login for local network
      </label>
      <p className="mt-1 text-xs text-amber-300">
        Warning: behind Docker port mapping or a reverse proxy, all traffic may appear to come from
        the local network, so this can effectively disable login for everyone.
      </p>
      <button type="submit" disabled={create.isPending} className={`mt-5 ${PRIMARY}`}>
        Create account
      </button>
    </form>
  );
}

// ---------- Step 2: Plex ----------

function PlexStep({ pollMs, onDone }: { pollMs: number; onDone: () => void }) {
  const queryClient = useQueryClient();
  const [pin, setPin] = useState<PlexPin | null>(null);
  const [pollError, setPollError] = useState<string | null>(null);
  const [pasting, setPasting] = useState(false);
  const [token, setToken] = useState("");
  const popup = useRef<Window | null>(null);
  const connected = () => {
    popup.current?.close();
    void queryClient.invalidateQueries({ queryKey: queryKeys.setupStatus });
    onDone();
  };
  // The poll loop calls the latest connected() without restarting when it changes.
  const connectedRef = useRef(connected);
  useEffect(() => {
    connectedRef.current = connected;
  });

  const startPin = useMutation({
    mutationFn: () => api.post<PlexPin>("/plex/pin"),
    onSuccess: (data) => {
      setPollError(null);
      setPin(data);
      if (popup.current && !popup.current.closed) {
        popup.current.opener = null; // Plex's page gets no handle back to Overlaparr
        popup.current.location.href = data.auth_url;
      }
    },
    onError: () => popup.current?.close(),
  });

  const saveToken = useMutation({
    mutationFn: (t: string) => api.put<PlexConnected>("/plex/token", { token: t }),
    onSuccess: connected,
  });

  // Poll the PIN until Plex reports a token; stops when done, on expiry and on unmount.
  useEffect(() => {
    if (!pin) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const res = await api.get<PlexConnected>(`/plex/pin/${pin.id}`);
        if (cancelled) return;
        if (res.connected) {
          connectedRef.current();
          return;
        }
      } catch (err) {
        if (cancelled) return;
        setPollError(message(err));
        if (err instanceof ApiError && err.status === 404) {
          setPin(null); // expired: the user starts over
          return;
        }
      }
      timer = setTimeout(poll, pollMs);
    };
    timer = setTimeout(poll, pollMs);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [pin, pollMs]);

  useEffect(() => () => popup.current?.close(), []);

  const signIn = () => {
    // Open the popup during the click so browsers don't block it; it is pointed at Plex
    // once the PIN exists.
    popup.current = window.open("", "plex-auth", "width=800,height=700");
    startPin.mutate();
  };

  const submitToken = (e: FormEvent) => {
    e.preventDefault();
    saveToken.mutate(token.trim());
  };

  return (
    <div>
      <StepHeading>Sign in with Plex</StepHeading>
      <p className="mt-1 text-sm text-slate-400">
        Overlaparr uses your Plex account to find the servers shared with you.
      </p>
      {startPin.isError && <Alert>{message(startPin.error)}</Alert>}
      {pollError && <Alert>{pollError}</Alert>}
      {pin ? (
        <p className="mt-4 text-sm text-slate-300">
          Waiting for you to approve Overlaparr in Plex…{" "}
          <a
            href={pin.auth_url}
            target="_blank"
            rel="noopener noreferrer"
            className="text-accent underline"
          >
            Open Plex sign-in
          </a>
        </p>
      ) : (
        <button
          type="button"
          onClick={signIn}
          disabled={startPin.isPending}
          className={`mt-4 ${PRIMARY}`}
        >
          Sign in with Plex
        </button>
      )}
      <div className="mt-5 border-t border-slate-700 pt-4">
        {pasting ? (
          <form onSubmit={submitToken}>
            <label className="block text-sm text-slate-300">
              Plex token
              <SecretInput
                className={INPUT}
                required
                value={token}
                onChange={(e) => setToken(e.target.value)}
              />
            </label>
            {saveToken.isError && <Alert>{message(saveToken.error)}</Alert>}
            <button type="submit" disabled={saveToken.isPending} className={`mt-3 ${SECONDARY}`}>
              Save token
            </button>
          </form>
        ) : (
          <button
            type="button"
            onClick={() => setPasting(true)}
            className="text-sm text-accent underline hover:text-accent-hover"
          >
            Paste a token instead
          </button>
        )}
      </div>
    </div>
  );
}

// ---------- Step 3: home server ----------

function HomeStep({ onDone }: { onDone: () => void }) {
  const queryClient = useQueryClient();
  // Refresh discovers servers with the new Plex token and returns the list.
  const servers = useQuery({
    queryKey: ["servers", "refresh"],
    queryFn: () => api.post<ServerRow[]>("/servers/refresh"),
    retry: false,
    refetchOnWindowFocus: false,
  });
  const [choice, setChoice] = useState<string | null>(null);
  const selected = choice ?? servers.data?.find((s) => s.is_home)?.machine_id ?? null;
  const setHome = useMutation({
    mutationFn: (machineId: string) => api.put<HomeResult>("/servers/home", { machine_id: machineId }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.setupStatus });
      onDone();
    },
  });

  return (
    <div>
      <StepHeading>Choose your home server</StepHeading>
      <p className="mt-1 text-sm text-slate-400">
        Your own Plex server, which your Sonarr and Radarr fill. Overlaparr looks for its media on
        the other servers.
      </p>
      {servers.isPending && <p className="mt-4 text-sm text-slate-400">Finding your servers…</p>}
      {servers.isError && (
        <>
          <Alert>{message(servers.error)}</Alert>
          <button type="button" onClick={() => void servers.refetch()} className={`mt-3 ${SECONDARY}`}>
            Retry
          </button>
        </>
      )}
      {servers.data && servers.data.length === 0 && (
        <p className="mt-4 text-sm text-slate-400">No Plex servers were found for this account.</p>
      )}
      {servers.data && servers.data.length > 0 && (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (selected) setHome.mutate(selected);
          }}
        >
          <fieldset className="mt-4 space-y-2">
            <legend className="sr-only">Home server</legend>
            {servers.data.map((s) => (
              <label
                key={s.machine_id}
                className="flex items-center gap-2 rounded border border-slate-700 px-3 py-2 text-sm text-slate-200 hover:bg-slate-700/50"
              >
                <input
                  type="radio"
                  name="home"
                  value={s.machine_id}
                  checked={selected === s.machine_id}
                  onChange={() => setChoice(s.machine_id)}
                />
                {s.label}
                {s.label !== s.name && <span className="text-slate-400">({s.name})</span>}
              </label>
            ))}
          </fieldset>
          {setHome.isError && <Alert>{message(setHome.error)}</Alert>}
          <button
            type="submit"
            disabled={!selected || setHome.isPending}
            className={`mt-4 ${PRIMARY}`}
          >
            Continue
          </button>
        </form>
      )}
    </div>
  );
}

// ---------- Step 4: instance ----------

const TYPE_LABEL = { sonarr: "Sonarr", radarr: "Radarr" } as const;

function InstanceStep({ onDone }: { onDone: (added: boolean) => void }) {
  const queryClient = useQueryClient();
  const instances = useQuery({
    queryKey: queryKeys.instances,
    queryFn: () => api.get<Instance[]>("/instances"),
  });
  const added = instances.data ?? [];
  const [adding, setAdding] = useState(false);
  // InstanceForm updates the instance it saved on later saves, so each new one gets a fresh form.
  const [formKey, setFormKey] = useState(0);

  const saved = (instance: Instance) => {
    // Show it right away; the form's invalidation then refetches the list from the server.
    queryClient.setQueryData<Instance[]>(queryKeys.instances, (old = []) =>
      old.some((i) => i.id === instance.id) ? old : [...old, instance],
    );
    setAdding(false);
  };

  return (
    <div>
      <StepHeading>Add your Sonarr and Radarr instances</StepHeading>
      <p className="mb-4 mt-1 text-sm text-slate-400">
        Overlaparr compares their libraries with the other Plex servers. You can add more later on
        the Instances page.
      </p>
      {added.length > 0 && (
        <ul aria-label="Added instances" className="mb-4 space-y-2">
          {added.map((i) => (
            <li
              key={i.id}
              className="flex items-center gap-2 rounded border border-slate-700 px-3 py-2 text-sm text-slate-200"
            >
              {i.name}
              <span className="text-slate-400">{TYPE_LABEL[i.type]}</span>
            </li>
          ))}
        </ul>
      )}
      {added.length === 0 || adding ? (
        <InstanceForm
          key={formKey}
          onSaved={saved}
          actions={
            added.length === 0 ? (
              <button
                type="button"
                onClick={() => onDone(false)}
                className="ml-auto text-sm text-slate-400 hover:text-slate-200"
              >
                Skip for now
              </button>
            ) : (
              <button
                type="button"
                onClick={() => setAdding(false)}
                className="ml-auto text-sm text-slate-400 hover:text-slate-200"
              >
                Cancel
              </button>
            )
          }
        />
      ) : (
        <div className="flex gap-3">
          <button
            type="button"
            onClick={() => {
              setFormKey((k) => k + 1);
              setAdding(true);
            }}
            className={SECONDARY}
          >
            Add another
          </button>
          <button type="button" onClick={() => onDone(true)} className={PRIMARY}>
            Continue
          </button>
        </div>
      )}
    </div>
  );
}

// ---------- Step 5: finish ----------

type ScanStart = "started" | "already-running";

/** Starts a scan; a 409 means one is already running. */
async function startScan(): Promise<ScanStart> {
  try {
    await api.post("/scan");
    return "started";
  } catch (err) {
    if (err instanceof ApiError && err.status === 409) return "already-running";
    throw err;
  }
}

function FinishStep({ instanceAdded, pollMs }: { instanceAdded: boolean; pollMs: number }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [waiting, setWaiting] = useState(false);
  const [waitError, setWaitError] = useState<string | null>(null);

  const done = () => {
    void queryClient.invalidateQueries({ queryKey: queryKeys.setupStatus });
    navigate("/", { replace: true });
  };
  // The wait loop calls the latest done() without restarting when it changes.
  const doneRef = useRef(done);
  useEffect(() => {
    doneRef.current = done;
  });

  const finish = useMutation({
    mutationFn: startScan,
    onSuccess: (result) => {
      // A scan that was already running (e.g. a scheduled one) read the config before the
      // instances were added, so it won't cover them: wait for it to end, then scan again.
      if (result === "already-running" && instanceAdded) setWaiting(true);
      else done();
    },
  });

  // Poll until the running scan ends, then start the full one. Stops on unmount.
  useEffect(() => {
    if (!waiting) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const status = await api.get<ScanStatus>("/scan/status");
        if (cancelled) return;
        if (!status.progress.running) {
          const result = await startScan();
          if (cancelled) return;
          if (result === "started") {
            doneRef.current();
            return;
          }
        }
        setWaitError(null);
      } catch (err) {
        if (cancelled) return;
        setWaitError(message(err));
      }
      timer = setTimeout(poll, pollMs);
    };
    timer = setTimeout(poll, pollMs);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [waiting, pollMs]);

  return (
    <div>
      <StepHeading>All set</StepHeading>
      <p className="mt-1 text-sm text-slate-400">
        Overlaparr will now index the Plex servers and look for overlaps. The first scan can take a
        while; you can follow it on the dashboard.
      </p>
      {finish.isError && <Alert>{message(finish.error)}</Alert>}
      {waiting ? (
        <>
          <p role="status" className="mt-4 text-sm text-slate-300">
            A scan is already running and doesn&apos;t include your instances — waiting for it to
            finish, then starting another…
          </p>
          {waitError && <Alert>{waitError}</Alert>}
          <p className="mt-3 text-xs text-amber-300">
            If you go to the dashboard now, run a scan once the current one finishes so your
            instances are included.
          </p>
          <button type="button" onClick={done} className={`mt-3 ${SECONDARY}`}>
            Go to dashboard now
          </button>
        </>
      ) : (
        <button
          type="button"
          onClick={() => finish.mutate()}
          disabled={finish.isPending}
          className={`mt-5 ${PRIMARY}`}
        >
          Finish and run first scan
        </button>
      )}
    </div>
  );
}

