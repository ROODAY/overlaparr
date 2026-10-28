import { QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import type { ReactElement } from "react";
import { MemoryRouter } from "react-router-dom";
import { createQueryClient } from "../api/queryClient";
import { ToastProvider } from "../components/Toast";

/** Renders inside a fresh query client, toast provider and router. */
export function renderWithProviders(ui: ReactElement, { route = "/" } = {}) {
  const queryClient = createQueryClient();
  return {
    queryClient,
    ...render(
      <QueryClientProvider client={queryClient}>
        <ToastProvider>
          <MemoryRouter initialEntries={[route]}>{ui}</MemoryRouter>
        </ToastProvider>
      </QueryClientProvider>,
    ),
  };
}
