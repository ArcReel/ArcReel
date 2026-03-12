import { create } from "zustand";
import type { ProjectData, ProjectSummary, EpisodeScript } from "@/types";

interface ProjectsState {
  // List
  projects: ProjectSummary[];
  projectsLoading: boolean;

  // Current project detail
  currentProjectName: string | null;
  currentProjectData: ProjectData | null;
  currentScripts: Record<string, EpisodeScript>;
  projectDetailLoading: boolean;

  // Create modal
  showCreateModal: boolean;
  creatingProject: boolean;

  // Asset fingerprints (path → mtime_ns)
  assetFingerprints: Record<string, number>;

  // Actions
  setProjects: (projects: ProjectSummary[]) => void;
  setProjectsLoading: (loading: boolean) => void;
  setCurrentProject: (
    name: string | null,
    data: ProjectData | null,
    scripts?: Record<string, EpisodeScript>,
    fingerprints?: Record<string, number>,
  ) => void;
  setProjectDetailLoading: (loading: boolean) => void;
  setShowCreateModal: (show: boolean) => void;
  setCreatingProject: (creating: boolean) => void;
  updateAssetFingerprints: (fps: Record<string, number>) => void;
  getAssetFingerprint: (path: string) => number | null;
}

export const useProjectsStore = create<ProjectsState>((set, get) => ({
  projects: [],
  projectsLoading: false,
  currentProjectName: null,
  currentProjectData: null,
  currentScripts: {},
  projectDetailLoading: false,
  showCreateModal: false,
  creatingProject: false,
  assetFingerprints: {},

  setProjects: (projects) => set({ projects }),
  setProjectsLoading: (loading) => set({ projectsLoading: loading }),
  setCurrentProject: (name, data, scripts, fingerprints) =>
    set((s) => ({
      currentProjectName: name,
      currentProjectData: data,
      currentScripts: scripts ?? {},
      assetFingerprints: fingerprints ?? s.assetFingerprints,
    })),
  setProjectDetailLoading: (loading) => set({ projectDetailLoading: loading }),
  setShowCreateModal: (show) => set({ showCreateModal: show }),
  setCreatingProject: (creating) => set({ creatingProject: creating }),
  updateAssetFingerprints: (fps) =>
    set((s) => ({ assetFingerprints: { ...s.assetFingerprints, ...fps } })),
  getAssetFingerprint: (path) => get().assetFingerprints[path] ?? null,
}));
