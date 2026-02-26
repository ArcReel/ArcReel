import { create } from "zustand";

interface UsageFilters {
  project_name?: string;
  media_type?: string;
  status?: string;
}

interface UsageStats {
  total_cost: number;
  image_count: number;
  video_count: number;
  failed_count: number;
  total_count: number;
}

interface UsageCall {
  id: string;
  project_name: string;
  media_type: string;
  model: string;
  status: string;
  cost: number;
  created_at: string;
  [key: string]: unknown;
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
