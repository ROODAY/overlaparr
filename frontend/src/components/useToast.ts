import { createContext, useContext } from "react";

export type ToastVariant = "info" | "error";

export type ShowToast = (message: string, variant?: ToastVariant) => void;

export const ToastContext = createContext<ShowToast | null>(null);

/** Returns show(message, variant?). Must be used inside <ToastProvider>. */
export function useToast(): ShowToast {
  const show = useContext(ToastContext);
  if (!show) throw new Error("useToast must be used inside <ToastProvider>");
  return show;
}
