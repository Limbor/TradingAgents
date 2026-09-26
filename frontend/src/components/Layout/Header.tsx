import { useEffect, useState } from "react";
import { useLocation } from "react-router-dom";
import { useRunStore } from "@/stores/useRunStore";
import { Activity, CircleCheck, CircleX, Clock3, Monitor, Moon, Sun } from "lucide-react";

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
  const [theme, setTheme] = useState<"system" | "light" | "dark">(() => {
    try {
      const saved = window.localStorage.getItem("tradingagents.theme");
      return saved === "light" || saved === "dark" ? saved : "system";
    } catch {
      return "system";
    }
  });

  useEffect(() => {
    if (theme === "system") document.documentElement.removeAttribute("data-theme");
    else document.documentElement.setAttribute("data-theme", theme);
    try {
      if (theme === "system") window.localStorage.removeItem("tradingagents.theme");
      else window.localStorage.setItem("tradingagents.theme", theme);
    } catch { /* In-memory switching still works without storage. */ }
  }, [theme]);

  const ThemeIcon = theme === "light" ? Sun : theme === "dark" ? Moon : Monitor;
  const themeLabel = theme === "light" ? "浅色" : theme === "dark" ? "深色" : "跟随系统";
  const themeButton = <button
    type="button"
    aria-label={`主题：${themeLabel}，点击切换`}
    title={`主题：${themeLabel}，点击切换`}
    onClick={() => setTheme(theme === "system" ? "light" : theme === "light" ? "dark" : "system")}
    className="rounded-md border border-ui-line p-1.5 text-ui-muted hover:bg-ui-hover hover:text-ui-ink"
  ><ThemeIcon className="h-4 w-4" /></button>;

  return <header className="flex h-[52px] shrink-0 items-center justify-between gap-3 border-b border-ui-line bg-ui-panel px-4 sm:px-5">
    <div className="min-w-0 text-xs text-ui-muted"><span className="hidden sm:inline">工作空间</span><span className="mx-2 hidden text-ui-faint sm:inline">/</span><strong className="truncate font-semibold text-ui-ink">{title}</strong></div>
    <div className="flex items-center gap-2">{pathname !== "/chat" && <div className={`hidden items-center gap-2 rounded-md border border-ui-line px-2.5 py-1.5 text-xs sm:flex ${running ? "text-ui-accent" : failed ? "text-ui-danger" : "text-ui-muted"}`}><StatusIcon className={`h-3.5 w-3.5 ${running ? "animate-pulse" : ""}`} />{running ? "任务运行中" : failed ? "任务需检查" : status === "completed" ? "任务已完成" : "等待任务"}</div>}{themeButton}</div>
  </header>;
}
