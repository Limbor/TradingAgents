import { activityDetail, toolAction } from "@/utils/activityLabels";
import { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { FileText, TerminalSquare } from "lucide-react";

interface ReportSection {
  section: string;
  content: string;
  is_final: boolean;
}

interface ToolCall {
  status?: string;
  tool: string;
  args: Record<string, unknown>;
  timestamp: string;
}

interface ReportPanelProps {
  sections: Record<string, ReportSection>;
  toolCalls: ToolCall[];
}

const SECTION_TITLES: Record<string, string> = {
  market_report: "行情走势",
  sentiment_report: "市场情绪",
  news_report: "新闻公告",
  fundamentals_report: "基本面",
  investment_plan: "多空研究",
  trader_investment_plan: "交易计划",
  final_trade_decision: "最终判断",
};

const SECTION_ORDER = [
  "market_report",
  "sentiment_report",
  "news_report",
  "fundamentals_report",
  "investment_plan",
  "trader_investment_plan",
  "final_trade_decision",
];

export function ReportPanel({ sections, toolCalls }: ReportPanelProps) {
  const availableSections = SECTION_ORDER.filter((key) => sections[key]);
  const [activeSection, setActiveSection] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<"report" | "tools">("report");

  const displaySection = activeSection ?? availableSections[availableSections.length - 1] ?? null;

  return (
    <div className="flex h-full flex-col">
      {/* Top tabs: Report / Tools */}
      <div className="mb-3 flex items-center justify-between border-b border-ui-line pb-3">
        <div>
          <h3 className="text-sm font-semibold text-ui-body">研究报告</h3>
          <p className="text-xs text-ui-faint">分析结论与数据查询记录</p>
        </div>
        <div className="flex rounded-lg border border-ui-line bg-ui-subtle p-1">
        <button
          onClick={() => setActiveTab("report")}
          className={`flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium transition ${activeTab === "report" ? "bg-ui-hover text-ui-ink" : "text-ui-faint hover:text-ui-body"}`}
        >
          <FileText className="h-3.5 w-3.5" />
          报告
        </button>
        <button
          onClick={() => setActiveTab("tools")}
          className={`flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium transition ${activeTab === "tools" ? "bg-ui-hover text-ui-ink" : "text-ui-faint hover:text-ui-body"}`}
        >
          <TerminalSquare className="h-3.5 w-3.5" />
          数据查询 ({toolCalls.length})
        </button>
        </div>
      </div>

      {activeTab === "report" && (
        <>
          {/* Section tabs */}
          {availableSections.length > 0 && (
            <div className="mb-3 flex flex-wrap gap-1.5">
              {availableSections.map((key) => (
                <button
                  key={key}
                  onClick={() => setActiveSection(key)}
                  className={`rounded-md px-3 py-1.5 text-xs font-medium transition-colors ${
                    displaySection === key
                      ? "bg-ui-accent text-ui-onAccent"
                      : "border border-ui-line bg-ui-subtle text-ui-muted hover:text-ui-ink"
                  }`}
                >
                  {SECTION_TITLES[key] ?? key}
                </button>
              ))}
            </div>
          )}

          {/* Report content */}
          <div className="min-h-0 flex-1 overflow-y-auto rounded-lg border border-ui-line bg-ui-subtle p-4">
            {displaySection && sections[displaySection] ? (
              <div className="prose prose-invert prose-sm max-w-none">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>
                  {sections[displaySection]!.content}
                </ReactMarkdown>
              </div>
            ) : (
              <p className="text-sm text-ui-faint">Waiting for report content...</p>
            )}
          </div>
        </>
      )}

      {activeTab === "tools" && (
        <div className="min-h-0 flex-1 space-y-2 overflow-y-auto rounded-lg border border-ui-line bg-ui-subtle p-3 text-xs">
          {toolCalls.length === 0 ? (
            <p className="text-ui-faint">暂无数据查询记录</p>
          ) : (
            toolCalls.map((call, i) => (
              <div key={i} className="rounded-md border border-ui-line bg-ui-panel p-2">
                <p className="break-words text-ui-body">{toolAction(call.tool)} · {call.status === "completed" ? "已完成" : call.status === "failed" ? "失败" : "已发起"}</p>
                <span className="text-ui-faint">
                  {" "}
                  {activityDetail(call.args)}
                </span>
              </div>
            ))
          )}
        </div>
      )}
    </div>
  );
}
