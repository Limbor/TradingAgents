import { useCallback, useEffect, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  type ConfigResponse,
  getConfig,
  getProfile,
  listProviders,
  updateConfig,
  updateProfile,
  type UserProfile,
} from "../../api/client";
import { getAuthToken, setAuthToken } from "../../api/auth";
import { queryKeys } from "@/api/queryKeys";
import { ModelPicker } from "../AgentWorkspace/ModelPicker";

const LANGUAGES = [
  { label: "English", value: "English" },
  { label: "Chinese (中文)", value: "Chinese" },
  { label: "Japanese (日本語)", value: "Japanese" },
  { label: "Korean (한국어)", value: "Korean" },
  { label: "French (Français)", value: "French" },
  { label: "Spanish (Español)", value: "Spanish" },
  { label: "German (Deutsch)", value: "German" },
];

export default function Settings() {
  const queryClient = useQueryClient();
  const configQuery = useQuery({ queryKey: queryKeys.config(), queryFn: getConfig });
  const providersQuery = useQuery({
    queryKey: queryKeys.providers(),
    queryFn: listProviders,
  });
  const profileQuery = useQuery({ queryKey: queryKeys.profile(), queryFn: getProfile });

  const [config, setConfig] = useState<ConfigResponse | null>(null);
  const [profile, setProfile] = useState<UserProfile | null>(null);
  const [saving, setSaving] = useState(false);
  const [customDeep, setCustomDeep] = useState("");
  const [editingDeepModel, setEditingDeepModel] = useState(false);
  const [customAgent, setCustomAgent] = useState("");
  const [editingAgentModel, setEditingAgentModel] = useState(false);
  const [customUrl, setCustomUrl] = useState("");
  const [authToken, setAuthTokenState] = useState("");
  const [saveError, setSaveError] = useState("");

  // Sync fetched config to local state
  useEffect(() => {
    setAuthTokenState(getAuthToken());
  }, []);

  useEffect(() => {
    if (configQuery.data) {
      setConfig(configQuery.data);
      setCustomUrl(configQuery.data.backend_url ?? "");
      setCustomAgent(configQuery.data.model_policy?.default_model || configQuery.data.agent_model || configQuery.data.quick_think_llm);
      setCustomDeep(configQuery.data.model_policy?.deep_model || "");
    }
  }, [configQuery.data]);

  useEffect(() => {
    if (profileQuery.data && !profile) {
      setProfile(profileQuery.data);
    }
  }, [profileQuery.data, profile]);

  const currentProvider = useMemo(
    () => providersQuery.data?.find((p) => p.id === config?.llm_provider),
    [providersQuery.data, config?.llm_provider]
  );
  const agentModels = useMemo(() => {
    const options = [...(currentProvider?.quick_models ?? []), ...(currentProvider?.deep_models ?? [])];
    return options.filter((option, index) => option.value !== "custom" &&
      options.findIndex((candidate) => candidate.value === option.value) === index);
  }, [currentProvider]);

  const save = useCallback(
    async (updates: Partial<ConfigResponse>) => {
      setSaving(true);
      setSaveError("");
      try {
        const updated = await updateConfig(updates);
        setConfig(updated);
        queryClient.setQueryData(queryKeys.config(), updated);
        return true;
      } catch (e) {
        setSaveError(e instanceof Error ? e.message : "配置保存失败");
        return false;
      } finally {
        setSaving(false);
      }
    },
    [queryClient]
  );

  const saveProfile = useCallback(
    async (updates: Partial<UserProfile>) => {
      setSaving(true);
      try {
        const updated = await updateProfile(updates);
        setProfile(updated);
      } catch (e) {
        console.error("Failed to save profile:", e);
      } finally {
        setSaving(false);
      }
    },
    []
  );

  const handleProviderChange = useCallback(
    (providerId: string) => {
      const pd = providersQuery.data?.find((p) => p.id === providerId);
      if (!pd) return;

      // Auto-select first model for each tier
      const quick = pd.quick_models.find((model) => model.value !== "custom");
      if (quick) {
        setCustomDeep("");
        setCustomAgent("");
        setEditingAgentModel(false);
        setEditingDeepModel(false);
        setCustomUrl("");
        save({
          llm_provider: providerId,
          model_policy: { default_model: quick.value, deep_model: null },
          backend_url: null,
        });
      }
    },
    [providersQuery.data, save]
  );

  if (!config || configQuery.isLoading) {
    return (
      <div className="flex h-64 items-center justify-center">
        <p className="text-ui-muted">加载配置中…</p>
      </div>
    );
  }

  const defaultModel = config.model_policy?.default_model || config.agent_model || config.quick_think_llm;
  const deepModel = config.model_policy ? config.model_policy.deep_model : (config.deep_think_llm !== defaultModel ? config.deep_think_llm : null);

  const apiKeyConfigured =
    config.api_keys[config.llm_provider] ?? false;

  return (
    <div className="mx-auto max-w-2xl space-y-6">
      <div className="flex items-center justify-between">
        <h2 className="text-2xl font-bold">设置</h2>
        {saving && (
          <span className="text-sm text-ui-muted">保存中…</span>
        )}
      </div>
      {saveError && <p role="alert" className="rounded border border-ui-danger/40 bg-ui-danger/10 px-3 py-2 text-sm text-ui-danger">{saveError}</p>}

      {/* LLM Backbone */}
      <section className="rounded-lg border border-ui-strong bg-ui-hover/50 p-5">
        <h3 className="mb-2 text-lg font-semibold">模型配置</h3>
        <p className="mb-4 text-sm leading-6 text-ui-muted">对话、股票分析、板块研究和复盘共用同一模型配置。设置对新任务生效，运行中的任务继续使用启动时的配置。</p>
        <div className="rounded-lg border border-ui-line bg-ui-panel p-4">
          <p className="mb-2 text-sm font-medium">当前模型</p>
          <ModelPicker config={configQuery.data} onSavingChange={setSaving} />
          <p className="mt-2 text-xs leading-5 text-ui-muted">与聊天输入框下方同步。切换会让所有角色使用所选模型。</p>
        </div>
        {config.llm_provider === "qianwen" && <label className="mt-4 flex items-center gap-2 text-sm">
          <input type="checkbox" checked={config.qianwen_thinking ?? false} onChange={(event) => void save({ qianwen_thinking: event.target.checked })} />
          启用千问深度思考<span className="text-xs text-ui-muted">增加耗时与 Token 用量</span>
        </label>}
        <details className="mt-4 rounded-lg border border-ui-line bg-ui-panel p-4">
          <summary className="cursor-pointer text-sm font-medium">高级模型设置</summary>
          <p className="mb-4 mt-3 text-xs leading-5 text-ui-muted">自定义模型与服务地址，以及可选的研究裁决模型覆盖。普通切换请使用上方菜单。</p>
          <div className="space-y-4">
          <div>
            <label className="mb-1 block text-sm text-ui-body">模型服务商</label>
            <select
              value={config.llm_provider}
              onChange={(e) => handleProviderChange(e.target.value)}
              className="w-full rounded-lg border border-ui-strong bg-ui-panel px-3 py-2 text-sm focus:border-ui-accent focus:outline-none"
            >
              {providersQuery.data?.filter((p) =>
                ["qianwen", "deepseek", config.llm_provider].includes(p.id) ||
                (config.api_keys[p.id] && !["ollama", "bedrock", "openai_compatible"].includes(p.id))
              ).map((p) => (
                <option key={p.id} value={p.id} disabled={!config.api_keys[p.id] && p.id !== config.llm_provider}>
                  {p.name}{!config.api_keys[p.id] ? "（未配置密钥）" : ""}
                </option>
              ))}
            </select>
          </div>

          <div className="rounded-lg border border-ui-line bg-ui-panel p-4">
            <label htmlFor="default-model" className="mb-1 block text-sm font-medium text-ui-ink">默认模型</label>
            <p className="mb-3 text-xs leading-5 text-ui-muted">所有 Agent 默认使用此模型。可在高级设置中为研究裁决与组合决策指定深度模型。</p>
            <select
              id="default-model"
              value={editingAgentModel || (!agentModels.some((model) => model.value === defaultModel)) ? "custom" : defaultModel}
              onChange={(event) => {
                if (event.target.value === "custom") {
                  setCustomAgent(defaultModel);
                  setEditingAgentModel(true);
                } else {
                  setEditingAgentModel(false);
                  setCustomAgent("");
                  void save({ model_policy: { default_model: event.target.value, deep_model: deepModel } });
                }
              }}
              className="w-full rounded-lg border border-ui-strong bg-ui-panel px-3 py-2 text-sm focus:border-ui-accent focus:outline-none"
            >
              {agentModels.map((model) => <option key={model.value} value={model.value}>{model.label}</option>)}
              <option value="custom">自定义模型 ID…</option>
            </select>
            {(editingAgentModel || (!agentModels.some((model) => model.value === defaultModel))) &&
              <div className="mt-3 flex flex-wrap gap-2">
                <input
                  aria-label="自定义默认模型 ID"
                  type="text"
                  value={customAgent}
                  onChange={(event) => setCustomAgent(event.target.value)}
                  placeholder="输入服务商支持的模型 ID"
                  className="min-w-0 flex-1 rounded-lg border border-ui-strong bg-ui-panel px-3 py-2 text-sm focus:border-ui-accent focus:outline-none"
                />
                <button type="button" disabled={!customAgent.trim() || saving}
                  onClick={async () => { if (await save({ model_policy: { default_model: customAgent.trim(), deep_model: deepModel } })) setEditingAgentModel(false); }}
                  className="rounded-lg bg-ui-accent px-3 py-2 text-sm text-ui-onAccent disabled:opacity-50">保存模型</button>
              </div>}
          </div>

          <div>
            <label className="mb-1 block text-sm text-ui-body">
              服务地址{" "}
              <span className="text-ui-faint">（可选，留空使用默认地址）</span>
            </label>
            <input
              type="text"
              placeholder="e.g. https://api.openai.com/v1"
              value={customUrl}
              onChange={(e) => setCustomUrl(e.target.value)}
              onBlur={() => save({ backend_url: customUrl || null } as Partial<ConfigResponse>)}
              className="w-full rounded-lg border border-ui-strong bg-ui-panel px-3 py-2 text-sm focus:border-ui-accent focus:outline-none"
            />
          </div>

          <div className="rounded-lg border border-ui-line bg-ui-panel p-4">
            <label htmlFor="deep-model" className="mb-1 mt-4 block text-sm text-ui-body">深度模型（可选）</label>
            <p className="mb-3 text-xs leading-5 text-ui-muted">用于研究经理裁决与组合决策。留空时，所有角色使用默认模型。</p>
            <select id="deep-model"
              value={editingDeepModel || (deepModel && !agentModels.some((m) => m.value === deepModel)) ? "custom" : deepModel ?? ""}
              onChange={(event) => {
                if (event.target.value === "custom") { setEditingDeepModel(true); setCustomDeep(deepModel ?? ""); }
                else { setEditingDeepModel(false); void save({ model_policy: { default_model: defaultModel, deep_model: event.target.value || null } }); }
              }}
              className="w-full rounded-lg border border-ui-strong bg-ui-panel px-3 py-2 text-sm focus:border-ui-accent focus:outline-none">
              <option value="">跟随默认模型（{defaultModel}）</option>
              {agentModels.map((model) => <option key={model.value} value={model.value}>{model.label}</option>)}
              <option value="custom">自定义模型 ID…</option>
            </select>
            {(editingDeepModel || (deepModel && !agentModels.some((m) => m.value === deepModel))) &&
              <div className="mt-3 flex gap-2">
                <input aria-label="自定义深度模型 ID" value={customDeep} onChange={(event) => setCustomDeep(event.target.value)}
                  className="min-w-0 flex-1 rounded-lg border border-ui-strong bg-ui-panel px-3 py-2 text-sm" />
                <button type="button" disabled={!customDeep.trim() || saving}
                  onClick={async () => { if (await save({ model_policy: { default_model: defaultModel, deep_model: customDeep.trim() } })) setEditingDeepModel(false); }}
                  className="rounded-lg bg-ui-accent px-3 py-2 text-sm text-ui-onAccent disabled:opacity-50">保存深度模型</button>
              </div>}
          </div>
        </div>
        </details>
      </section>

      {/* API Key Status */}
      <section className="rounded-lg border border-ui-strong bg-ui-hover/50 p-5">
        <h3 className="mb-4 text-lg font-semibold">模型密钥状态</h3>
        <div className="flex items-center gap-3">
          <span
            className={`h-2.5 w-2.5 rounded-full ${apiKeyConfigured ? "bg-ui-accent" : "bg-ui-danger"}`}
          />
          <span className="text-sm">
            {currentProvider?.name ?? config.llm_provider}:{" "}
            {apiKeyConfigured ? (
              <span className="text-ui-accent">API 密钥已配置</span>
            ) : (
              <span className="text-ui-danger">
                缺少 API 密钥，请设置对应环境变量
              </span>
            )}
          </span>
        </div>
        <p className="mt-3 text-xs leading-5 text-ui-muted">密钥在后端 .env 中配置，修改后重启后端。千问使用 QIANWEN_API_KEY，DeepSeek 使用 DEEPSEEK_API_KEY。</p>
      </section>

      {profile && (
        <section className="rounded-lg border border-ui-strong bg-ui-hover/50 p-5">
          <h3 className="mb-4 text-lg font-semibold">分析偏好</h3>
          <div className="grid gap-4 md:grid-cols-2">
            <div>
              <label className="mb-1 block text-sm text-ui-body">投资周期</label>
              <select
                value={profile.investment_style}
                onChange={(e) =>
                  saveProfile({
                    investment_style: e.target.value as UserProfile["investment_style"],
                  })
                }
                className="w-full rounded-lg border border-ui-strong bg-ui-panel px-3 py-2 text-sm focus:border-ui-accent focus:outline-none"
              >
                <option value="short_term">短线</option>
                <option value="medium_term">中期</option>
                <option value="long_term">长期</option>
              </select>
            </div>
            <div>
              <label className="mb-1 block text-sm text-ui-body">风险偏好</label>
              <select
                value={profile.risk_tolerance}
                onChange={(e) =>
                  saveProfile({
                    risk_tolerance: e.target.value as UserProfile["risk_tolerance"],
                  })
                }
                className="w-full rounded-lg border border-ui-strong bg-ui-panel px-3 py-2 text-sm focus:border-ui-accent focus:outline-none"
              >
                <option value="low">保守</option>
                <option value="moderate">均衡</option>
                <option value="high">积极</option>
              </select>
            </div>
          </div>
          <div className="mt-4">
            <label className="mb-1 block text-sm text-ui-body">关注行业</label>
            <input
              type="text"
              value={profile.sector_prefs.join(", ")}
              onChange={(e) =>
                setProfile({
                  ...profile,
                  sector_prefs: e.target.value
                    .split(/[,，、]/)
                    .map((item) => item.trim())
                    .filter(Boolean),
                })
              }
              onBlur={() => saveProfile({ sector_prefs: profile.sector_prefs })}
              className="w-full rounded-lg border border-ui-strong bg-ui-panel px-3 py-2 text-sm focus:border-ui-accent focus:outline-none"
            />
          </div>
        </section>
      )}

      {/* Output */}
      <section className="rounded-lg border border-ui-strong bg-ui-hover/50 p-5">
        <h3 className="mb-4 text-lg font-semibold">输出设置</h3>
        <div>
          <label className="mb-1 block text-sm text-ui-body">输出语言</label>
          <select
            value={config.output_language}
            onChange={(e) => save({ output_language: e.target.value })}
            className="w-full rounded-lg border border-ui-strong bg-ui-panel px-3 py-2 text-sm focus:border-ui-accent focus:outline-none"
          >
            {LANGUAGES.map((l) => (
              <option key={l.value} value={l.value}>
                {l.label}
              </option>
            ))}
          </select>
        </div>
      </section>

      <details className="rounded-lg border border-ui-line bg-ui-panel p-5">
        <summary className="cursor-pointer font-medium">高级系统设置</summary>
        <p className="mb-4 mt-3 text-xs text-ui-muted">连接、访问认证与分析工作流参数。</p>
        <div className="space-y-4">
      {/* StockManager MCP */}
      <section className="rounded-lg border border-ui-strong bg-ui-hover/50 p-5">
        <h3 className="mb-4 text-lg font-semibold">StockManager MCP</h3>
        <div className="space-y-4">
          <label className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={config.stockmanager_mcp_enabled}
              onChange={(e) =>
                save({ stockmanager_mcp_enabled: e.target.checked })
              }
              className="h-4 w-4 rounded border-ui-strong bg-ui-panel"
            />
            <span className="text-sm text-ui-body">
              启用本地 StockManager MCP 服务
            </span>
          </label>
          <div>
            <label className="mb-1 block text-sm text-ui-body">MCP URL</label>
            <input
              type="text"
              value={config.stockmanager_mcp_url ?? ""}
              onChange={(e) =>
                setConfig({ ...config, stockmanager_mcp_url: e.target.value })
              }
              onBlur={() =>
                save({ stockmanager_mcp_url: config.stockmanager_mcp_url })
              }
              className="w-full rounded-lg border border-ui-strong bg-ui-panel px-3 py-2 text-sm focus:border-ui-accent focus:outline-none"
            />
          </div>
          <div>
            <label className="mb-1 block text-sm text-ui-body">
              工具超时
              <span className="ml-1 text-ui-faint">（秒）</span>
            </label>
            <input
              type="number"
              min={1}
              value={config.stockmanager_mcp_timeout}
              onChange={(e) =>
                setConfig({
                  ...config,
                  stockmanager_mcp_timeout: Number(e.target.value),
                })
              }
              onBlur={() =>
                save({ stockmanager_mcp_timeout: config.stockmanager_mcp_timeout })
              }
              className="w-full rounded-lg border border-ui-strong bg-ui-panel px-3 py-2 text-sm focus:border-ui-accent focus:outline-none"
            />
          </div>
        </div>
      </section>

      {/* API Access Token */}
      <section className="rounded-lg border border-ui-strong bg-ui-hover/50 p-5">
        <h3 className="mb-2 text-lg font-semibold">API 访问令牌</h3>
        <p className="mb-3 text-sm text-ui-muted">
          当后端设置 <code className="text-ui-body">TRADINGAGENTS_API_AUTH_TOKEN</code> 后，所有 REST/WebSocket
          请求需携带此令牌。本地默认不设可留空。
        </p>
        <div className="flex gap-2">
          <input
            type="password"
            value={authToken}
            onChange={(e) => setAuthTokenState(e.target.value)}
            placeholder="留空则不启用认证"
            className="flex-1 rounded border border-ui-strong bg-ui-panel px-3 py-1.5 text-sm text-ui-ink"
          />
          <button
            type="button"
            onClick={() => {
              setAuthToken(authToken);
            }}
            className="rounded bg-ui-accent px-4 py-1.5 text-sm font-medium text-ui-onAccent hover:bg-ui-accent"
          >
            保存
          </button>
        </div>
      </section>

      {/* 分析工作流 */}
      <section className="rounded-lg border border-ui-strong bg-ui-hover/50 p-5">
        <h3 className="mb-4 text-lg font-semibold">分析工作流</h3>
        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className="mb-1 block text-sm text-ui-body">
              多空讨论轮次
              <span className="ml-1 text-ui-faint">(bull vs bear)</span>
            </label>
            <select
              value={config.max_debate_rounds}
              onChange={(e) =>
                save({ max_debate_rounds: parseInt(e.target.value) })
              }
              className="w-full rounded-lg border border-ui-strong bg-ui-panel px-3 py-2 text-sm focus:border-ui-accent focus:outline-none"
            >
              {[1, 2, 3, 4, 5].map((n) => (
                <option key={n} value={n}>
                  {n}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="mb-1 block text-sm text-ui-body">
              风险讨论轮次
              <span className="ml-1 text-ui-faint">(risk debate)</span>
            </label>
            <select
              value={config.max_risk_discuss_rounds}
              onChange={(e) =>
                save({ max_risk_discuss_rounds: parseInt(e.target.value) })
              }
              className="w-full rounded-lg border border-ui-strong bg-ui-panel px-3 py-2 text-sm focus:border-ui-accent focus:outline-none"
            >
              {[1, 2, 3, 4, 5].map((n) => (
                <option key={n} value={n}>
                  {n}
                </option>
              ))}
            </select>
          </div>
        </div>
        <div className="mt-4">
          <label className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={config.checkpoint_enabled}
              onChange={(e) =>
                save({ checkpoint_enabled: e.target.checked })
              }
              className="h-4 w-4 rounded border-ui-strong bg-ui-panel"
            />
            <span className="text-sm text-ui-body">
              启用断点恢复{" "}
              <span className="text-ui-faint">
                （异常后继续运行）
              </span>
            </span>
          </label>
        </div>
        <div className="mt-4">
          <label className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={config.adaptive_alpha_enabled}
              onChange={(e) =>
                save({ adaptive_alpha_enabled: e.target.checked })
              }
              className="h-4 w-4 rounded border-ui-strong bg-ui-panel"
            />
            <span className="text-sm text-ui-body">
              启用自适应 α{" "}
              <span className="text-ui-faint">
                (根据预测评分动态调整每日选股的 quant/LLM 融合权重；未生效时维持静态权重)
              </span>
            </span>
          </label>
        </div>
      </section>
        </div>
      </details>
    </div>
  );
}
