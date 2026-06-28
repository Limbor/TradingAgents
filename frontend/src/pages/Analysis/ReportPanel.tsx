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
  tool: string;
  args: Record<string, unknown>;
  timestamp: string;
}

interface ReportPanelProps {
  sections: Record<string, ReportSection>;
  toolCalls: ToolCall[];
}

const SECTION_TITLES: Record<string, string> = {
  market_report: "Market",
  sentiment_report: "Sentiment",
  news_report: "News",
  fundamentals_report: "Fundamentals",
  investment_plan: "Research",
  trader_investment_plan: "Trading",
  final_trade_decision: "Decision",
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
      <div className="mb-3 flex items-center justify-between border-b border-stone-800 pb-3">
        <div>
          <h3 className="text-sm font-semibold text-stone-200">Research Report</h3>
          <p className="text-xs text-stone-500">Streaming sections and tool telemetry</p>
        </div>
        <div className="flex rounded-lg border border-stone-800 bg-stone-950 p-1">
        <button
          onClick={() => setActiveTab("report")}
          className={`flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium transition ${activeTab === "report" ? "bg-stone-800 text-stone-50" : "text-stone-500 hover:text-stone-200"}`}
        >
          <FileText className="h-3.5 w-3.5" />
          Report
        </button>
        <button
          onClick={() => setActiveTab("tools")}
          className={`flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium transition ${activeTab === "tools" ? "bg-stone-800 text-stone-50" : "text-stone-500 hover:text-stone-200"}`}
        >
          <TerminalSquare className="h-3.5 w-3.5" />
          Tools ({toolCalls.length})
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
                      ? "bg-teal-500 text-stone-950"
                      : "border border-stone-800 bg-stone-950 text-stone-400 hover:text-stone-100"
                  }`}
                >
                  {SECTION_TITLES[key] ?? key}
                </button>
              ))}
            </div>
          )}

          {/* Report content */}
          <div className="min-h-0 flex-1 overflow-y-auto rounded-lg border border-stone-800 bg-stone-950 p-4">
            {displaySection && sections[displaySection] ? (
              <div className="prose prose-invert prose-sm max-w-none">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>
                  {sections[displaySection]!.content}
                </ReactMarkdown>
              </div>
            ) : (
              <p className="text-sm text-stone-500">Waiting for report content...</p>
            )}
          </div>
        </>
      )}

      {activeTab === "tools" && (
        <div className="min-h-0 flex-1 space-y-2 overflow-y-auto rounded-lg border border-stone-800 bg-stone-950 p-3 font-mono text-xs">
          {toolCalls.length === 0 ? (
            <p className="text-stone-500">No tool calls yet</p>
          ) : (
            toolCalls.map((call, i) => (
              <div key={i} className="rounded-md border border-stone-800 bg-stone-900 p-2">
                <span className="text-teal-300">{call.tool}</span>
                <span className="text-stone-500">
                  {" "}
                  ({JSON.stringify(call.args).slice(0, 120)})
                </span>
              </div>
            ))
          )}
        </div>
      )}
    </div>
  );
}
