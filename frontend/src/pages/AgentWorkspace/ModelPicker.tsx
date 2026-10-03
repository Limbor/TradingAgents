import * as Dropdown from "@radix-ui/react-dropdown-menu";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, ChevronDown, Cpu, Settings2 } from "lucide-react";
import { Link } from "react-router-dom";
import { listProviders, updateConfig, type ConfigResponse } from "@/api/client";
import type { TaskModelSelection } from "@/api/agent";
import { queryKeys } from "@/api/queryKeys";

export function ModelPicker({ config, onSavingChange, disabled }: {
  config?: ConfigResponse;
  onSavingChange?: (saving: boolean) => void;
  disabled?: boolean;
}) {
  const providers = useQuery({ queryKey: queryKeys.providers(), queryFn: listProviders, staleTime: 60_000, retry: false });
  const queryClient = useQueryClient();
  const selection = useMutation({
    mutationFn: (choice: TaskModelSelection) => updateConfig({
      llm_provider: choice.provider,
      model_policy: { default_model: choice.model, deep_model: null },
      ...(choice.provider !== config?.llm_provider ? { backend_url: null } : {}),
    }),
    onMutate: () => onSavingChange?.(true),
    onSuccess: (updated) => {
      queryClient.setQueryData(queryKeys.config(), updated);
      try { window.localStorage.removeItem("tradingagents.chat-model.v1"); } catch { /* Old selections are no longer used. */ }
    },
    onSettled: () => onSavingChange?.(false),
  });
  const availableProviders = Array.isArray(providers.data) ? providers.data : [];
  const defaultModel = config?.model_policy?.default_model || config?.agent_model || config?.quick_think_llm;
  const provider = availableProviders.find((item) => item.id === config?.llm_provider);
  const models = [...(provider?.quick_models || []), ...(provider?.deep_models || [])];
  const label = models.find((item) => item.value === defaultModel)?.label || defaultModel || "默认模型";

  return <div className="min-w-0"><Dropdown.Root>
    <Dropdown.Trigger asChild>
      <button type="button" aria-label="切换聊天模型" disabled={disabled || selection.isPending || !config}
        title={`${provider?.name || "默认渠道"} · 全系统共用，对新任务生效`}
        className="inline-flex min-w-0 max-w-[240px] items-center gap-1.5 rounded-md px-2 py-1 text-xs text-ui-muted hover:bg-ui-hover hover:text-ui-ink disabled:opacity-50">
        <Cpu className="h-3.5 w-3.5 shrink-0" /><span className="truncate">{selection.isPending ? "保存模型中…" : label}</span><ChevronDown className="h-3 w-3 shrink-0" />
      </button>
    </Dropdown.Trigger>
    <Dropdown.Portal>
      <Dropdown.Content side="top" align="start" sideOffset={8} collisionPadding={12}
        className="z-[100] max-h-[min(440px,70vh)] w-[min(300px,calc(100vw-24px))] overflow-y-auto rounded-xl border border-ui-line bg-ui-panel p-1.5 text-sm text-ui-body shadow-xl">
        <p className="px-2.5 py-2 text-xs leading-5 text-ui-muted">对话与分析共用此模型。切换对新任务生效。</p>
        {config?.model_policy?.deep_model && <p className="px-2.5 pb-2 text-xs text-ui-warning">研究裁决覆盖：{config.model_policy.deep_model}。重新选择将统一所有角色。</p>}
        <Dropdown.Separator className="my-1 h-px bg-ui-line" />
        {availableProviders.filter((item) => (config?.api_keys?.[item.id] && !["ollama", "bedrock", "openai_compatible"].includes(item.id)) ||
          ["qianwen", "deepseek", config?.llm_provider].includes(item.id)).map((item) => {
          const ready = Boolean(config?.api_keys?.[item.id]);
          const options = [...new Map([...item.quick_models, ...item.deep_models,
            ...(item.id === config?.llm_provider && defaultModel &&
              ![...item.quick_models, ...item.deep_models].some((model) => model.value === defaultModel)
              ? [{ value: defaultModel, label: defaultModel }] : [])]
            .filter((model) => model.value !== "custom").map((model) => [model.value, model])).values()];
          if (!options.length) return null;
          return <Dropdown.Group key={item.id}>
            <Dropdown.Label className="flex items-center justify-between gap-2 px-2.5 pb-1 pt-3 text-[11px] text-ui-muted">
              <span>{item.name}</span>{!ready && <span>{item.id === "qianwen" ? "需配置 QIANWEN_API_KEY" : "未配置密钥"}</span>}
            </Dropdown.Label>
            {options.map((model) => <Dropdown.Item key={model.value} disabled={!ready}
              onSelect={() => selection.mutate({ provider: item.id, model: model.value })}
              className="flex cursor-pointer items-center gap-2 rounded-md px-2.5 py-2 outline-none data-[highlighted]:bg-ui-hover data-[disabled]:cursor-default data-[disabled]:opacity-40">
              <span className="min-w-0 flex-1 truncate">{model.label}</span>
              {config?.llm_provider === item.id && defaultModel === model.value && <Check className="h-4 w-4 shrink-0 text-ui-accent" />}
            </Dropdown.Item>)}
          </Dropdown.Group>;
        })}
        {(providers.isError || (providers.isSuccess && !Array.isArray(providers.data))) && <p role="status" className="px-2.5 py-2 text-xs text-ui-warning">模型列表暂时无法加载，可重试或进入设置。</p>}
        <Dropdown.Separator className="my-1 h-px bg-ui-line" />
        <Dropdown.Item asChild><Link to="/settings" className="flex items-center gap-2 rounded-md px-2.5 py-2 text-xs text-ui-muted outline-none data-[highlighted]:bg-ui-hover"><Settings2 className="h-3.5 w-3.5" />管理模型与渠道</Link></Dropdown.Item>
      </Dropdown.Content>
    </Dropdown.Portal>
  </Dropdown.Root>{selection.isError && <p role="alert" className="mt-1 text-xs text-ui-danger">模型切换失败：{selection.error.message}。仍使用原模型。</p>}</div>;
}
