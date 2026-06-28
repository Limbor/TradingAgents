import { useCallback, useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  type ConfigResponse,
  getConfig,
  listProviders,
  updateConfig,
} from "../../api/client";

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
  const configQuery = useQuery({ queryKey: ["config"], queryFn: getConfig });
  const providersQuery = useQuery({
    queryKey: ["providers"],
    queryFn: listProviders,
  });

  const [config, setConfig] = useState<ConfigResponse | null>(null);
  const [saving, setSaving] = useState(false);
  const [customQuick, setCustomQuick] = useState("");
  const [customDeep, setCustomDeep] = useState("");
  const [customUrl, setCustomUrl] = useState("");

  // Sync fetched config to local state
  useEffect(() => {
    if (configQuery.data && !config) {
      setConfig(configQuery.data);
      setCustomUrl(configQuery.data.backend_url ?? "");
      // If current model is not in any dropdown, treat as custom
      const provider = configQuery.data.llm_provider;
      const pd = providersQuery.data?.find((p) => p.id === provider);
      if (pd) {
        const hasQuick = pd.quick_models.some(
          (m) => m.value === configQuery.data.quick_think_llm
        );
        if (!hasQuick && configQuery.data.quick_think_llm !== "custom") {
          setCustomQuick(configQuery.data.quick_think_llm);
        }
        const hasDeep = pd.deep_models.some(
          (m) => m.value === configQuery.data.deep_think_llm
        );
        if (!hasDeep && configQuery.data.deep_think_llm !== "custom") {
          setCustomDeep(configQuery.data.deep_think_llm);
        }
      }
    }
  }, [configQuery.data, providersQuery.data, config]);

  const currentProvider = useMemo(
    () => providersQuery.data?.find((p) => p.id === config?.llm_provider),
    [providersQuery.data, config?.llm_provider]
  );

  const save = useCallback(
    async (updates: Partial<ConfigResponse>) => {
      setSaving(true);
      try {
        const updated = await updateConfig(updates);
        setConfig(updated);
      } catch (e) {
        console.error("Failed to save:", e);
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
      const quick = pd.quick_models[0];
      const deep = pd.deep_models[0];
      if (quick && deep) {
        setCustomQuick("");
        setCustomDeep("");
        save({
          llm_provider: providerId,
          quick_think_llm: quick.value,
          deep_think_llm: deep.value,
        });
      }
    },
    [providersQuery.data, save]
  );

  if (!config || configQuery.isLoading) {
    return (
      <div className="flex h-64 items-center justify-center">
        <p className="text-stone-400">Loading configuration...</p>
      </div>
    );
  }

  const apiKeyConfigured =
    config.api_keys[config.llm_provider] ?? false;

  return (
    <div className="mx-auto max-w-2xl space-y-6">
      <div className="flex items-center justify-between">
        <h2 className="text-2xl font-bold">Settings</h2>
        {saving && (
          <span className="text-sm text-stone-400">Saving...</span>
        )}
      </div>

      {/* API Key Status */}
      <section className="rounded-lg border border-stone-700 bg-stone-800/50 p-5">
        <h3 className="mb-4 text-lg font-semibold">API Key Status</h3>
        <div className="flex items-center gap-3">
          <span
            className={`h-2.5 w-2.5 rounded-full ${apiKeyConfigured ? "bg-teal-400" : "bg-red-400"}`}
          />
          <span className="text-sm">
            {currentProvider?.name ?? config.llm_provider}:{" "}
            {apiKeyConfigured ? (
              <span className="text-teal-400">API key configured</span>
            ) : (
              <span className="text-red-400">
                API key missing — set the corresponding environment variable
              </span>
            )}
          </span>
        </div>
      </section>

      {/* LLM Provider */}
      <section className="rounded-lg border border-stone-700 bg-stone-800/50 p-5">
        <h3 className="mb-4 text-lg font-semibold">LLM Provider</h3>
        <div className="space-y-4">
          <div>
            <label className="mb-1 block text-sm text-stone-300">Provider</label>
            <select
              value={config.llm_provider}
              onChange={(e) => handleProviderChange(e.target.value)}
              className="w-full rounded-lg border border-stone-600 bg-stone-900 px-3 py-2 text-sm focus:border-teal-500 focus:outline-none"
            >
              {providersQuery.data?.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
          </div>

          <div>
            <label className="mb-1 block text-sm text-stone-300">
              Backend URL{" "}
              <span className="text-stone-500">(optional, leave empty for default)</span>
            </label>
            <input
              type="text"
              placeholder="e.g. https://api.openai.com/v1"
              value={customUrl}
              onChange={(e) => setCustomUrl(e.target.value)}
              onBlur={() => save({ backend_url: customUrl || null } as Partial<ConfigResponse>)}
              className="w-full rounded-lg border border-stone-600 bg-stone-900 px-3 py-2 text-sm focus:border-teal-500 focus:outline-none"
            />
          </div>
        </div>
      </section>

      {/* Model Selection */}
      <section className="rounded-lg border border-stone-700 bg-stone-800/50 p-5">
        <h3 className="mb-4 text-lg font-semibold">Model Selection</h3>
        <div className="space-y-4">
          {/* Quick Think Model */}
          <div>
            <label className="mb-1 block text-sm text-stone-300">
              Quick Think Model{" "}
              <span className="text-stone-500">(analysts, debates, trader)</span>
            </label>
            <select
              value={
                currentProvider?.quick_models.some(
                  (m) => m.value === config.quick_think_llm
                )
                  ? config.quick_think_llm
                  : "custom"
              }
              onChange={(e) => {
                if (e.target.value === "custom") {
                  // Keep current custom value
                } else {
                  setCustomQuick("");
                  save({ quick_think_llm: e.target.value });
                }
              }}
              className="w-full rounded-lg border border-stone-600 bg-stone-900 px-3 py-2 text-sm focus:border-teal-500 focus:outline-none"
            >
              {currentProvider?.quick_models.map((m) => (
                <option key={m.value} value={m.value}>
                  {m.label}
                </option>
              ))}
              {!currentProvider?.quick_models.some(
                (m) => m.value === config.quick_think_llm
              ) && (
                <option value="custom">{config.quick_think_llm}</option>
              )}
            </select>
            {(config.quick_think_llm === "custom" ||
              !currentProvider?.quick_models.some(
                (m) => m.value === config.quick_think_llm
              )) && (
              <input
                type="text"
                placeholder="Custom model ID"
                value={customQuick || config.quick_think_llm}
                onChange={(e) => setCustomQuick(e.target.value)}
                onBlur={() => {
                  if (customQuick) save({ quick_think_llm: customQuick });
                }}
                className="mt-2 w-full rounded-lg border border-stone-600 bg-stone-900 px-3 py-2 text-sm focus:border-teal-500 focus:outline-none"
              />
            )}
          </div>

          {/* Deep Think Model */}
          <div>
            <label className="mb-1 block text-sm text-stone-300">
              Deep Think Model{" "}
              <span className="text-stone-500">(research manager, portfolio manager)</span>
            </label>
            <select
              value={
                currentProvider?.deep_models.some(
                  (m) => m.value === config.deep_think_llm
                )
                  ? config.deep_think_llm
                  : "custom"
              }
              onChange={(e) => {
                if (e.target.value === "custom") {
                  // Keep current custom value
                } else {
                  setCustomDeep("");
                  save({ deep_think_llm: e.target.value });
                }
              }}
              className="w-full rounded-lg border border-stone-600 bg-stone-900 px-3 py-2 text-sm focus:border-teal-500 focus:outline-none"
            >
              {currentProvider?.deep_models.map((m) => (
                <option key={m.value} value={m.value}>
                  {m.label}
                </option>
              ))}
              {!currentProvider?.deep_models.some(
                (m) => m.value === config.deep_think_llm
              ) && (
                <option value="custom">{config.deep_think_llm}</option>
              )}
            </select>
            {(config.deep_think_llm === "custom" ||
              !currentProvider?.deep_models.some(
                (m) => m.value === config.deep_think_llm
              )) && (
              <input
                type="text"
                placeholder="Custom model ID"
                value={customDeep || config.deep_think_llm}
                onChange={(e) => setCustomDeep(e.target.value)}
                onBlur={() => {
                  if (customDeep) save({ deep_think_llm: customDeep });
                }}
                className="mt-2 w-full rounded-lg border border-stone-600 bg-stone-900 px-3 py-2 text-sm focus:border-teal-500 focus:outline-none"
              />
            )}
          </div>
        </div>
      </section>

      {/* Output */}
      <section className="rounded-lg border border-stone-700 bg-stone-800/50 p-5">
        <h3 className="mb-4 text-lg font-semibold">Output Settings</h3>
        <div>
          <label className="mb-1 block text-sm text-stone-300">Output Language</label>
          <select
            value={config.output_language}
            onChange={(e) => save({ output_language: e.target.value })}
            className="w-full rounded-lg border border-stone-600 bg-stone-900 px-3 py-2 text-sm focus:border-teal-500 focus:outline-none"
          >
            {LANGUAGES.map((l) => (
              <option key={l.value} value={l.value}>
                {l.label}
              </option>
            ))}
          </select>
        </div>
      </section>

      {/* Analysis Settings */}
      <section className="rounded-lg border border-stone-700 bg-stone-800/50 p-5">
        <h3 className="mb-4 text-lg font-semibold">Analysis Settings</h3>
        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className="mb-1 block text-sm text-stone-300">
              Debate Rounds
              <span className="ml-1 text-stone-500">(bull vs bear)</span>
            </label>
            <select
              value={config.max_debate_rounds}
              onChange={(e) =>
                save({ max_debate_rounds: parseInt(e.target.value) })
              }
              className="w-full rounded-lg border border-stone-600 bg-stone-900 px-3 py-2 text-sm focus:border-teal-500 focus:outline-none"
            >
              {[1, 2, 3, 4, 5].map((n) => (
                <option key={n} value={n}>
                  {n}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="mb-1 block text-sm text-stone-300">
              Risk Rounds
              <span className="ml-1 text-stone-500">(risk debate)</span>
            </label>
            <select
              value={config.max_risk_discuss_rounds}
              onChange={(e) =>
                save({ max_risk_discuss_rounds: parseInt(e.target.value) })
              }
              className="w-full rounded-lg border border-stone-600 bg-stone-900 px-3 py-2 text-sm focus:border-teal-500 focus:outline-none"
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
              className="h-4 w-4 rounded border-stone-600 bg-stone-900"
            />
            <span className="text-sm text-stone-300">
              Enable checkpoint/resume{" "}
              <span className="text-stone-500">
                (recover from crashes on next run)
              </span>
            </span>
          </label>
        </div>
      </section>
    </div>
  );
}
