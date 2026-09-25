import { useLocation } from "react-router-dom";
import { useRunStore } from "@/stores/useRunStore";
import { Activity, CircleCheck, CircleX, Clock3 } from "lucide-react";

const titles: Record<string, string> = {
  "/": "决策工作台", "/chat": "交易 Agent", "/research": "策略研究", "/paper": "模拟盘",
  "/audit": "决策审计", "/market": "市场全景", "/portfolio": "持仓管理",
  "/library": "产物库", "/reflection": "反思记录", "/settings": "设置", "/analysis": "分析详情",
};

export function Header() {
  const { pathname } = useLocation();
  const status = useRunStore((state) => state.status);
  const title = titles[pathname] ?? (pathname.startsWith("/analysis/") ? titles["/analysis"] : "工作台");
  const running = status === "running";
  const failed = status === "failed" || status === "cancelled";
  const StatusIcon = running ? Activity : failed ? CircleX : status === "completed" ? CircleCheck : Clock3;

  if (pathname === "/chat") return <header className="flex h-[52px] shrink-0 items-center justify-between border-b border-[#dfe6df] bg-white px-4 text-[#1f2922] sm:px-5"><div className="text-xs text-[#68776d]">工作空间 <span className="mx-2 text-[#b5c3b8]">/</span><strong className="font-semibold text-[#1f2922]">交易 Agent</strong></div><span className="text-xs text-[#68776d]">交易决策工作台</span></header>;

  return <header className="flex h-14 shrink-0 items-center justify-between gap-3 border-b border-stone-800 bg-[#202622] px-4 sm:px-6 md:h-[72px]">
    <div className="min-w-0 text-sm text-stone-500"><span className="hidden sm:inline">工作空间</span><span className="mx-2 hidden text-stone-700 sm:inline">/</span><strong className="truncate font-medium text-stone-100">{title}</strong></div>
    <div className={`flex items-center gap-2 rounded-full border border-stone-700/60 px-3 py-1.5 text-xs ${running ? "text-teal-300" : failed ? "text-rose-300" : "text-stone-400"}`}><StatusIcon className={`h-3.5 w-3.5 ${running ? "animate-pulse" : ""}`} />{running ? "任务运行中" : failed ? "任务需检查" : status === "completed" ? "任务已完成" : "等待任务"}</div>
  </header>;
}
