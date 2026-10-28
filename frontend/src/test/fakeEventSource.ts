import type { EventSourceLike } from "../features/scan/useScanEvents";
import type { ScanProgress } from "../api/types";

/** Controllable stand-in for EventSource (jsdom has none). */
export class FakeEventSource implements EventSourceLike {
  static instances: FakeEventSource[] = [];

  onopen: ((ev: Event) => void) | null = null;
  onmessage: ((ev: MessageEvent) => void) | null = null;
  onerror: ((ev: Event) => void) | null = null;
  closed = false;
  readonly url: string;

  constructor(url: string) {
    this.url = url;
    FakeEventSource.instances.push(this);
  }

  static get latest(): FakeEventSource {
    const last = FakeEventSource.instances.at(-1);
    if (!last) throw new Error("no EventSource was opened");
    return last;
  }

  static reset(): void {
    FakeEventSource.instances = [];
  }

  open(): void {
    this.onopen?.(new Event("open"));
  }

  emit(state: ScanProgress): void {
    this.onmessage?.(new MessageEvent("message", { data: JSON.stringify(state) }));
  }

  fail(): void {
    this.onerror?.(new Event("error"));
  }

  close(): void {
    this.closed = true;
  }
}

export const fakeEventSourceFactory = (url: string) => new FakeEventSource(url);
