import { QueryClient } from "@tanstack/react-query";
import { ApiError } from "./client";

/** Shared query keys for data several components read. */
export const queryKeys = {
  setupStatus: ["setup-status"],
  me: ["me"],
  dashboard: ["dashboard"],
  instances: ["instances"],
  /** Rules list and warnings; also the prefix for preview queries. */
  rules: ["rules"],
  scanStatus: ["scan-status"],
  /** Prefix for every findings list query (the full key adds the query string). */
  findings: ["findings"],
  /** One finding's detail; under the findings prefix so list invalidations refresh it. */
  findingDetail: (id: string) => ["findings", "detail", id] as const,
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
