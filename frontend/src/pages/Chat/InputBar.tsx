import { FormEvent } from "react";
import { Rocket, Send, ShieldAlert, Sparkles, TrendingUp } from "lucide-react";
import {
  dailyPipelineHint,
  marketScannerHint,
  riskMonitorHint,
  type IntentHint,
} from "@/lib/chatNav";

const QUICK_ACTIONS: Array<{ label: string; prompt: string; icon: typeof Sparkles; hint?: IntentHint }> = [
  { label: "每日选股", prompt: "每日选股 top 5", icon: Sparkles, hint: dailyPipelineHint(5) },
  { label: "风险扫描", prompt: "分析当前持仓风险", icon: ShieldAlert, hint: riskMonitorHint() },
  { label: "分析个股...", prompt: "", icon: TrendingUp },
  { label: "扫描A股", prompt: "筛选A股 top 3 score 60", icon: Rocket, hint: marketScannerHint(3, 60) },
];
const PAPER_ACTIONS: typeof QUICK_ACTIONS = [
  { label: "解释策略切换", prompt: "为什么这个模拟盘选择或切换了当前子策略？", icon: Sparkles },
  { label: "核对成交", prompt: "核对这个模拟盘近期成交与当前持仓", icon: TrendingUp },
  { label: "检查下日计划", prompt: "解释这个模拟盘的下一交易日计划和风险", icon: ShieldAlert },
];

interface InputBarProps {
  input: string;
  setInput: (value: string) => void;
  canSend: boolean;
  running: boolean;
  onSubmit: (e: FormEvent) => void;
  onQuickAction: (prompt: string, hint?: IntentHint) => void;
  paperMode?: boolean;
}

export function InputBar({ input, setInput, canSend, running, onSubmit, onQuickAction, paperMode = false }: InputBarProps) {
  return (
    <div className="border-t border-ui-line pt-3">
      <form onSubmit={onSubmit} className="flex gap-2">
        <input
          value={input}
          onChange={(event) => setInput(event.target.value)}
          placeholder={paperMode ? "问这次换仓、收益、风险或下一交易日计划..." : "例如: 帮我看看茅台 / 每日选股 / 分析持仓风险..."}
          className="min-w-0 flex-1 rounded-lg border border-ui-strong bg-ui-panel px-4 py-2.5 text-sm outline-none transition focus:border-ui-accent"
        />
        <button
          disabled={!canSend}
          className="inline-flex items-center gap-2 rounded-lg bg-ui-accent px-5 py-2.5 text-sm font-semibold text-ui-onAccent transition hover:bg-ui-accent disabled:cursor-not-allowed disabled:opacity-50"
        >
          <Send className="h-4 w-4" />
        </button>
      </form>

      {/* Quick action chips */}
      <div className="mt-2.5 flex flex-wrap gap-2 pb-1">
        {(paperMode ? PAPER_ACTIONS : QUICK_ACTIONS).map((action) => (
          <button
            key={action.label}
            onClick={() => onQuickAction(action.prompt, action.hint)}
            disabled={running && !!action.prompt}
            className="inline-flex items-center gap-1.5 rounded-full border border-ui-strong bg-ui-panel px-3 py-1.5 text-xs text-ui-body transition hover:border-ui-accent/50 hover:text-ui-ink disabled:opacity-50"
          >
            <action.icon className="h-3.5 w-3.5 text-ui-accent" />
            {action.label}
          </button>
        ))}
      </div>
    </div>
  );
}
