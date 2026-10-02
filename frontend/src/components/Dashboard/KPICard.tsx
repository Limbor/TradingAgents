import type { Activity } from "lucide-react";

interface KPICardProps {
  icon: typeof Activity;
  label: string;
  value: string;
  sub: string;
  tone?: "teal" | "emerald" | "amber" | "red";
}

const tones = {
  teal: "text-ui-accent",
  emerald: "text-ui-success",
  amber: "text-ui-warning",
  red: "text-ui-danger",
};

export function KPICard({ icon: Icon, label, value, sub, tone = "teal" }: KPICardProps) {
  return (
    <div className="rounded-lg border border-ui-line bg-ui-panel p-4">
      <div className={`mb-2 flex items-center gap-2 text-xs ${tones[tone]}`}>
        <Icon className="h-4 w-4" />
        <span>{label}</span>
      </div>
      <div className="font-mono text-xl font-semibold text-ui-ink">{value}</div>
      <div className="mt-1 text-xs text-ui-faint">{sub}</div>
    </div>
  );
}
