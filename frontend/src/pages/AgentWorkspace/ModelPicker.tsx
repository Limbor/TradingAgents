import * as Dropdown from "@radix-ui/react-dropdown-menu";
import { useQuery } from "@tanstack/react-query";
import { Check, ChevronDown, Cpu, Settings2 } from "lucide-react";
import { Link } from "react-router-dom";
import { listProviders, type ConfigResponse } from "@/api/client";
import type { TaskModelSelection } from "@/api/agent";
import { queryKeys } from "@/api/queryKeys";

const storageKey = "tradingagents.chat-model.v1";

export function savedChatModel(): TaskModelSelection | undefined {
  try {
    const value = JSON.parse(window.localStorage.getItem(storageKey) || "null");
    if (value && typeof value.provider === "string" && typeof value.model === "string") return value;
  } catch { /* The picker still works without browser storage. */ }
  return undefined;
}

export function ModelPicker({ config, value, onChange, disabled }: {
  config?: ConfigResponse;
  value?: TaskModelSelection;
  onChange: (selection?: TaskModelSelection) => void;
  disabled?: boolean;
}) {
  const providers = useQuery({ queryKey: queryKeys.providers(), queryFn: listProviders, staleTime: 60_000, retry: false });
  const availableProviders = Array.isArray(providers.data) ? providers.data : [];
  const defaultModel = config?.model_policy?.default_model || config?.agent_model || config?.quick_think_llm;
  const provider = availableProviders.find((item) => item.id === (value?.provider || config?.llm_provider));
  const models = [...(provider?.quick_models || []), ...(provider?.deep_models || [])];
  const label = models.find((item) => item.value === (value?.model || defaultModel))?.label || value?.model || defaultModel || "默认模型";
  const choose = (selection?: TaskModelSelection) => {
    onChange(selection);
    try {
      if (selection) window.localStorage.setItem(storageKey, JSON.stringify(selection));
      else window.localStorage.removeItem(storageKey);
    } catch { /* Selection is also held in React state. */ }
  };

  return <Dropdown.Root>
    <Dropdown.Trigger asChild>
      <button type="button" aria-label="切换聊天模型" disabled={disabled}
        title={`${provider?.name || "默认渠道"} · ${value ? "后续消息使用此模型" : "跟随全局默认配置"}`}
        className="inline-flex min-w-0 max-w-[240px] items-center gap-1.5 rounded-md px-2 py-1 text-xs text-ui-muted hover:bg-ui-hover hover:text-ui-ink disabled:opacity-50">
        <Cpu className="h-3.5 w-3.5 shrink-0" /><span className="truncate">{label}</span><ChevronDown className="h-3 w-3 shrink-0" />
      </button>
    </Dropdown.Trigger>
    <Dropdown.Portal>
      <Dropdown.Content side="top" align="start" sideOffset={8} collisionPadding={12}
        className="z-[100] max-h-[min(440px,70vh)] w-[min(300px,calc(100vw-24px))] overflow-y-auto rounded-xl border border-ui-line bg-ui-panel p-1.5 text-sm text-ui-body shadow-xl">
        <Dropdown.Item onSelect={() => choose()} className="flex cursor-pointer items-center gap-2 rounded-md px-2.5 py-2 outline-none data-[highlighted]:bg-ui-hover">
          <span className="flex-1">跟随默认模型<span className="mt-0.5 block truncate text-xs text-ui-muted">{defaultModel || "在设置中配置"}</span></span>
          {!value && <Check className="h-4 w-4 text-ui-accent" />}
        </Dropdown.Item>
        <Dropdown.Separator className="my-1 h-px bg-ui-line" />
        {availableProviders.filter((item) => config?.api_keys?.[item.id] ||
          ["qianwen", "deepseek", config?.llm_provider].includes(item.id)).map((item) => {
          const ready = Boolean(config?.api_keys?.[item.id]);
          const options = [...new Map([...item.quick_models, ...item.deep_models].filter((model) => model.value !== "custom").map((model) => [model.value, model])).values()];
          if (!options.length) return null;
          return <Dropdown.Group key={item.id}>
            <Dropdown.Label className="flex items-center justify-between gap-2 px-2.5 pb-1 pt-3 text-[11px] text-ui-muted">
              <span>{item.name}</span>{!ready && <span>{item.id === "qianwen" ? "需配置 QIANWEN_API_KEY" : "未配置密钥"}</span>}
            </Dropdown.Label>
            {options.map((model) => <Dropdown.Item key={model.value} disabled={!ready}
              onSelect={() => choose({ provider: item.id, model: model.value })}
              className="flex cursor-pointer items-center gap-2 rounded-md px-2.5 py-2 outline-none data-[highlighted]:bg-ui-hover data-[disabled]:cursor-default data-[disabled]:opacity-40">
              <span className="min-w-0 flex-1 truncate">{model.label}</span>
              {value?.provider === item.id && value.model === model.value && <Check className="h-4 w-4 shrink-0 text-ui-accent" />}
            </Dropdown.Item>)}
          </Dropdown.Group>;
        })}
        {(providers.isError || (providers.isSuccess && !Array.isArray(providers.data))) && <p role="status" className="px-2.5 py-2 text-xs text-ui-warning">模型列表暂时无法加载，可重试或进入设置。</p>}
        <Dropdown.Separator className="my-1 h-px bg-ui-line" />
        <Dropdown.Item asChild><Link to="/settings" className="flex items-center gap-2 rounded-md px-2.5 py-2 text-xs text-ui-muted outline-none data-[highlighted]:bg-ui-hover"><Settings2 className="h-3.5 w-3.5" />管理模型与渠道</Link></Dropdown.Item>
      </Dropdown.Content>
    </Dropdown.Portal>
  </Dropdown.Root>;
}
