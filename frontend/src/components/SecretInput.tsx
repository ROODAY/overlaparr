import { useState, type InputHTMLAttributes } from "react";

/** Password-style input with a Show/Hide toggle, for API keys and tokens. */
export function SecretInput({
  className,
  ...props
}: Omit<InputHTMLAttributes<HTMLInputElement>, "type">) {
  const [shown, setShown] = useState(false);
  return (
    <div className="relative">
      <input
        {...props}
        type={shown ? "text" : "password"}
        autoComplete="off"
        className={`${className ?? ""} pr-16`}
      />
      <button
        type="button"
        aria-pressed={shown}
        onClick={() => setShown((s) => !s)}
        className="absolute inset-y-0 right-0 mt-1 px-3 text-xs text-slate-400 hover:text-slate-200"
      >
        {shown ? "Hide" : "Show"}
      </button>
    </div>
  );
}
