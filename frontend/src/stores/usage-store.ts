import { create } from "zustand";

interface UsageFilters {
  project_name?: string;
  media_type?: string;
  status?: string;
}

interface UsageStats {
  total_cost: number;
  cost_by_currency: Record<string, number>;
  image_count: number;
  video_count: number;
  text_count: number;
  failed_count: number;
  total_count: number;
}

interface UsageCall {
  id: string;
  project_name: string;
  call_type: string;
  model: string;
  status: string;
  cost_amount: number;
  currency: string;
  provider: string;
  output_path: string | null;
  resolution: string | null;
  duration_seconds: number | null;
  duration_ms: number | null;
  error_message: string | null;
  started_at: string;
  created_at: string;
  input_tokens: number | null;
  output_tokens: number | null;
}

interface UsageState {
  projects: string[];
  filters: UsageFilters;
  stats: UsageStats | null;
  calls: UsageCall[];
  total: number;
  page: number;
  pageSize: number;
  loading: boolean;

  setProjects: (projects: string[]) => void;
  setFilters: (filters: UsageFilters) => void;
  setStats: (stats: UsageStats | null) => void;
  setCalls: (calls: UsageCall[], total: number) => void;
  setPage: (page: number) => void;
  setLoading: (loading: boolean) => void;
}

export const useUsageStore = create<UsageState>((set) => ({
  projects: [],
  filters: {},
  stats: null,
  calls: [],
  total: 0,
  page: 1,
  pageSize: 20,
  loading: false,

  setProjects: (projects) => set({ projects }),
  setFilters: (filters) => set({ filters }),
  setStats: (stats) => set({ stats }),
  setCalls: (calls, total) => set({ calls, total }),
  setPage: (page) => set({ page }),
  setLoading: (loading) => set({ loading }),
}));
