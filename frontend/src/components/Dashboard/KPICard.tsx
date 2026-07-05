import type { Activity } from "lucide-react";

interface KPICardProps {
  icon: typeof Activity;
  label: string;
  value: string;
  sub: string;
  tone?: "teal" | "emerald" | "amber" | "red";
}

const tones = {
  teal: "text-teal-300",
  emerald: "text-emerald-300",
  amber: "text-amber-300",
  red: "text-red-300",
};

export function KPICard({ icon: Icon, label, value, sub, tone = "teal" }: KPICardProps) {
  return (
    <div className="rounded-lg border border-stone-800 bg-stone-900 p-4">
      <div className={`mb-2 flex items-center gap-2 text-xs ${tones[tone]}`}>
        <Icon className="h-4 w-4" />
        <span>{label}</span>
      </div>
      <div className="font-mono text-xl font-semibold text-stone-50">{value}</div>
      <div className="mt-1 text-xs text-stone-500">{sub}</div>
    </div>
  );
}
