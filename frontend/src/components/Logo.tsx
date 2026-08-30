export function Logo({ size = 36 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 100 100" aria-hidden="true">
      <circle cx="50" cy="50" r="42" fill="none" stroke="#6D5EF5" strokeWidth="7" opacity="0.32" />
      <circle cx="50" cy="50" r="27" fill="none" stroke="#6D5EF5" strokeWidth="7" />
      <rect x="24" y="34" width="52" height="9" rx="2" fill="#F5F4FF" />
      <rect x="45.5" y="34" width="9" height="43" rx="2" fill="#F5F4FF" />
    </svg>
  );
}
