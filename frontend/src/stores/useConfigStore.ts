import { create } from "zustand";
import { type ConfigResponse, getConfig, updateConfig } from "../api/client";

interface ConfigState {
  config: ConfigResponse | null;
  loading: boolean;
  error: string | null;

  fetchConfig: () => Promise<void>;
  updateConfig: (updates: Partial<ConfigResponse>) => Promise<void>;
}

export const useConfigStore = create<ConfigState>((set) => ({
  config: null,
  loading: false,
  error: null,

  fetchConfig: async () => {
    set({ loading: true, error: null });
    try {
      const config = await getConfig();
      set({ config, loading: false });
    } catch (e) {
      set({ error: (e as Error).message, loading: false });
    }
  },

  updateConfig: async (updates) => {
    set({ loading: true, error: null });
    try {
      const config = await updateConfig(updates);
      set({ config, loading: false });
    } catch (e) {
      set({ error: (e as Error).message, loading: false });
    }
  },
}));
