export function Placeholder({ name }: { name: string }) {
  return (
    <div>
      <h1 className="text-2xl font-semibold text-slate-100">{name}</h1>
      <p className="mt-2 text-slate-400">{name} — coming soon</p>
    </div>
  );
}
