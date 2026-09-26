import { useState } from "react";
import { ArrowRight, FileSearch, Sparkles } from "lucide-react";
import type { ArtifactInfo } from "@/api/client";

interface DecisionCandidate {
  symbol: string;
  name: string;
  quantDecision: string;
  finalDecision: string;
  score: number | null;
  reasons: string[];
}

function candidatesFrom(artifact: ArtifactInfo | undefined): DecisionCandidate[] {
  const payload = artifact?.payload ?? {};
  const raw = Array.isArray(payload.decision_pack) ? payload.decision_pack : payload.candidates;
  if (!Array.isArray(raw)) return [];
  return raw.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object" && !Array.isArray(item))
    .filter((item) => typeof item.symbol === "string")
    .slice(0, 8)
    .map((item) => ({
      symbol: String(item.symbol), name: String(item.name || item.symbol),
      quantDecision: String(item.quant_decision || "—"),
      finalDecision: String(item.final_decision || item.signal || "—"),
      score: typeof item.display_score === "number" ? item.display_score : typeof item.final_score === "number" ? item.final_score : null,
      reasons: Array.isArray(item.gate_reasons) ? item.gate_reasons.filter((reason): reason is string => typeof reason === "string") : [],
    }));
}

const decisionLabels: Record<string, string> = { BUY: "买入", WATCHLIST: "观察", SKIP: "暂缓", HOLD: "持有", AVOID: "回避" };
const decisionLabel = (value: string) => decisionLabels[value.toUpperCase()] ?? value;

export function DecisionCandidatesPanel({ artifacts, onOpen, onAsk }: {
  artifacts: ArtifactInfo[];
  onOpen: (artifact: ArtifactInfo) => void;
  onAsk: (artifact: ArtifactInfo, candidate: DecisionCandidate) => void;
}) {
  const artifact = artifacts.find((item) => item.artifact_type === "decision_pack" && candidatesFrom(item).length)
    ?? artifacts.find((item) => candidatesFrom(item).length);
  const candidates = candidatesFrom(artifact);
  const [selectedSymbol, setSelectedSymbol] = useState("");
  const selected = candidates.find((item) => item.symbol === selectedSymbol) ?? candidates[0];
  const payload = artifact?.payload ?? {};
  const asOf = String(payload.market_asof_date || payload.trade_date || artifact?.created_at?.slice(0, 10) || "未知");

  return <section className="space-y-3">
    <div className="flex flex-wrap items-end justify-between gap-2"><div><p className="text-xs font-semibold uppercase tracking-[0.17em] text-ui-accent">Decision flow</p><h3 className="mt-1 text-lg font-semibold">最新候选与证据</h3></div><span className="text-xs text-ui-faint">{artifact ? `${artifact.title} · 数据截至 ${asOf}` : "尚无可展示的候选产物"}</span></div>
    <div className="grid gap-3 lg:grid-cols-[minmax(0,1.45fr)_minmax(280px,0.85fr)]">
      <div className="min-w-0 overflow-hidden rounded-2xl border border-ui-line bg-ui-panel/70">
        {candidates.length ? <div className="overflow-x-auto"><table className="w-full min-w-[560px] text-left text-sm"><thead className="bg-ui-subtle/50 text-xs text-ui-faint"><tr><th className="px-4 py-3">标的</th><th>量化判断</th><th>Agent 判断</th><th>关键证据</th></tr></thead><tbody>{candidates.map((item) => <tr key={item.symbol} onClick={() => setSelectedSymbol(item.symbol)} className={`cursor-pointer border-t border-ui-line transition hover:bg-ui-accent/5 ${selected?.symbol === item.symbol ? "bg-ui-accent/5" : ""}`}><td className="px-4 py-3"><strong className="block font-medium text-ui-ink">{item.name}</strong><span className="text-xs text-ui-faint">{item.symbol}</span></td><td className="text-ui-body">{decisionLabel(item.quantDecision)}</td><td><span className="rounded-full bg-ui-accent/10 px-2 py-1 text-xs text-ui-accent">{decisionLabel(item.finalDecision)}</span></td><td className="max-w-52 truncate pr-4 text-xs text-ui-muted" title={item.reasons.join("；")}>{item.reasons[0] || "查看完整产物"}</td></tr>)}</tbody></table></div> : <div className="flex min-h-52 flex-col items-center justify-center gap-3 p-6 text-center"><FileSearch className="h-6 w-6 text-ui-faint" /><p className="text-sm text-ui-muted">暂无候选结果。运行每日选股后，这里会显示量化与 Agent 判断。</p></div>}
      </div>
      <aside className="rounded-2xl border border-ui-line bg-ui-panel/70 p-4"><div className="flex items-center gap-2 text-xs text-ui-accent"><Sparkles className="h-4 w-4" />候选详情</div>{selected && artifact ? <><h4 className="mt-3 text-base font-semibold">{selected.name}</h4><p className="mt-1 text-xs text-ui-faint">{selected.symbol} · 数据截至 {asOf}</p><div className="mt-4 space-y-2 border-y border-ui-line py-3 text-sm"><div className="flex justify-between"><span className="text-ui-faint">量化判断</span><strong className="font-medium">{decisionLabel(selected.quantDecision)}</strong></div><div className="flex justify-between"><span className="text-ui-faint">Agent 判断</span><strong className="font-medium text-ui-accent">{decisionLabel(selected.finalDecision)}</strong></div><div className="flex justify-between"><span className="text-ui-faint">展示分数</span><strong className="font-medium">{selected.score ?? "—"}</strong></div></div><p className="mt-3 text-xs leading-5 text-ui-muted">{selected.reasons.length ? selected.reasons.slice(0, 3).join("；") : artifact.summary || "查看产物以核对完整判断依据。"}</p><div className="mt-4 flex flex-wrap gap-2"><button onClick={() => onAsk(artifact, selected)} className="rounded-lg bg-ui-accent px-3 py-2 text-xs font-semibold text-ui-onAccent">问 Agent</button><button onClick={() => onOpen(artifact)} className="inline-flex items-center gap-1 rounded-lg border border-ui-strong px-3 py-2 text-xs text-ui-body">查看产物 <ArrowRight className="h-3.5 w-3.5" /></button></div></> : <p className="mt-3 text-sm leading-6 text-ui-faint">选择候选后，查看来源、判断和风险证据。</p>}</aside>
    </div>
  </section>;
}
