const SAFE_BOUND = 10;
const WARNING_BOUND = 25;

interface ThresholdBarProps {
  value: number;
  max?: number;
}

export function ThresholdBar({ value, max = 40 }: ThresholdBarProps) {
  const clamped = Math.max(0, Math.min(max, value));
  const pct = (clamped / max) * 100;
  const safePct = (SAFE_BOUND / max) * 100;
  const warningPct = ((WARNING_BOUND - SAFE_BOUND) / max) * 100;
  const dangerPct = ((max - WARNING_BOUND) / max) * 100;

  return (
    <div className="relative h-3 flex items-center">
      <div className="flex h-1.5 w-full overflow-hidden rounded-full">
        <div className="h-full bg-success" style={{ width: `${safePct}%` }} />
        <div className="h-full bg-warning" style={{ width: `${warningPct}%` }} />
        <div className="h-full bg-danger" style={{ width: `${dangerPct}%` }} />
      </div>
      <div
        className="absolute top-[-1px] h-3 w-3 -ml-1.5 rounded-full border-2 border-background bg-foreground"
        style={{ left: `${pct}%` }}
      />
    </div>
  );
}
