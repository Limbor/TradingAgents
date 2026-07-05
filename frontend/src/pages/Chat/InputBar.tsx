import { FormEvent } from "react";
import { Rocket, Send, ShieldAlert, Sparkles, TrendingUp } from "lucide-react";

const QUICK_ACTIONS = [
  { label: "每日选股", prompt: "每日选股 top 5", icon: Sparkles },
  { label: "风险扫描", prompt: "分析当前持仓风险", icon: ShieldAlert },
  { label: "分析个股...", prompt: "", icon: TrendingUp },
  { label: "扫描A股", prompt: "筛选A股 top 3 score 60", icon: Rocket },
];

interface InputBarProps {
  input: string;
  setInput: (value: string) => void;
  canSend: boolean;
  running: boolean;
  onSubmit: (e: FormEvent) => void;
  onQuickAction: (prompt: string) => void;
}

export function InputBar({ input, setInput, canSend, running, onSubmit, onQuickAction }: InputBarProps) {
  return (
    <div className="border-t border-stone-800 pt-3">
      <form onSubmit={onSubmit} className="flex gap-2">
        <input
          value={input}
          onChange={(event) => setInput(event.target.value)}
          placeholder="例如: 帮我看看茅台 / 每日选股 / 分析持仓风险..."
          className="min-w-0 flex-1 rounded-lg border border-stone-700 bg-stone-900 px-4 py-2.5 text-sm outline-none transition focus:border-teal-400"
        />
        <button
          disabled={!canSend}
          className="inline-flex items-center gap-2 rounded-lg bg-teal-500 px-5 py-2.5 text-sm font-semibold text-stone-950 transition hover:bg-teal-400 disabled:cursor-not-allowed disabled:opacity-50"
        >
          <Send className="h-4 w-4" />
        </button>
      </form>

      {/* Quick action chips */}
      <div className="mt-2.5 flex flex-wrap gap-2 pb-1">
        {QUICK_ACTIONS.map((action) => (
          <button
            key={action.label}
            onClick={() => onQuickAction(action.prompt)}
            disabled={running && !!action.prompt}
            className="inline-flex items-center gap-1.5 rounded-full border border-stone-700 bg-stone-900 px-3 py-1.5 text-xs text-stone-300 transition hover:border-teal-500/50 hover:text-stone-100 disabled:opacity-50"
          >
            <action.icon className="h-3.5 w-3.5 text-teal-300" />
            {action.label}
          </button>
        ))}
      </div>
    </div>
  );
}
