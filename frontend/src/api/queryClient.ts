import { QueryClient } from "@tanstack/react-query";
import { ApiError } from "./client";

/** Shared query keys for data several components read. */
export const queryKeys = {
  setupStatus: ["setup-status"],
  me: ["me"],
  dashboard: ["dashboard"],
} as const;

export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        // Client errors (401, 404, 422...) will not fix themselves on retry.
        retry: (failures, error) =>
          !(error instanceof ApiError && error.status < 500) && failures < 2,
      },
    },
  });
}
