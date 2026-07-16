export function SummaryStat({ label, value, className }: { label: string; value: number; className?: string }) {
  return (
    <div className="text-center">
      <div className={`text-xl font-bold ${className ?? "text-foreground"}`}>{value}</div>
      <div className="text-[10px] font-medium text-muted-foreground uppercase tracking-wide">{label}</div>
    </div>
  );
}
