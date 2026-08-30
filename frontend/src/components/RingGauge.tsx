import { scoreColor } from "@/lib/scoreColor";

interface RingGaugeProps {
  value: number;
  size?: number;
  strokeWidth?: number;
  label?: string;
  colorClassName?: string;
  valueLabel?: string;
  valueFontSize?: number;
}

export function RingGauge({
  value,
  size = 88,
  strokeWidth = 8,
  label,
  colorClassName,
  valueLabel,
  valueFontSize = 20,
}: RingGaugeProps) {
  const clamped = Math.max(0, Math.min(100, value));
  const radius = (size - strokeWidth) / 2;
  const circumference = 2 * Math.PI * radius;
  const offset = circumference - (clamped / 100) * circumference;
  const center = size / 2;
  const colorClass = colorClassName ?? scoreColor(clamped);

  return (
    <div className="relative shrink-0" style={{ width: size, height: size }}>
      <svg width={size} height={size}>
        <circle cx={center} cy={center} r={radius} stroke="var(--color-surface-2)" strokeWidth={strokeWidth} fill="none" />
        <circle
          cx={center}
          cy={center}
          r={radius}
          className={colorClass}
          stroke="currentColor"
          strokeWidth={strokeWidth}
          fill="none"
          strokeDasharray={`${circumference} ${circumference}`}
          strokeDashoffset={offset}
          strokeLinecap="round"
          transform={`rotate(-90 ${center} ${center})`}
        />
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        <span className="font-bold text-foreground" style={{ fontSize: valueFontSize }}>
          {valueLabel ?? Math.round(clamped)}
        </span>
        {label && <span className="mt-0.5 text-[9px] text-muted">{label}</span>}
      </div>
    </div>
  );
}
