import { recorded } from "./recorded.ts";
import type { ApiOverrides } from "./test.ts";

export interface RecordedWorkspace {
  project: Record<string, unknown> & {
    episodes: Record<string, unknown>[];
  };
  summary: Record<string, unknown> & {
    currency: "registered";
    needs_repair: boolean;
    repair_reason: string | null;
    assets: Record<string, { total: number; available: number }>;
    episodes_summary: { total: number; scripted: number; in_production: number; completed: number };
    episodes: Array<{
      episode: number;
      script_status: string;
      status: string;
      item_count: number;
      duration_seconds: number;
      needs_replan: number;
      videos: { total: number; available: number };
      storyboards: { total: number; available: number };
    }>;
    source_remaining: boolean;
  };
  asset_fingerprints: Record<string, number>;
}

export interface RecordedScriptSnapshot {
  script: Record<string, unknown>;
  revision: string;
}

const BASE_WORKSPACE = recorded<RecordedWorkspace>("project-demo-workspace.json");
const BASE_SCRIPT_1 = recorded<RecordedScriptSnapshot>("project-demo-script-episode-1.json");
const BASE_EPISODE_META = BASE_WORKSPACE.project.episodes[0];
const BASE_EPISODE_SUMMARY = BASE_WORKSPACE.summary.episodes[0];

export function recordedDemoWorkspace(): RecordedWorkspace {
  return structuredClone(BASE_WORKSPACE);
}

export function recordedDemoScript(): RecordedScriptSnapshot {
  return structuredClone(BASE_SCRIPT_1);
}

export function recordedDemoEpisode(): Record<string, unknown> {
  return structuredClone(BASE_EPISODE_META);
}

export function buildWorkspaceBody(
  projectPatch: Record<string, unknown> = {},
  options: {
    status?: Record<string, unknown>;
    asset_fingerprints?: Record<string, number>;
  } = {},
): RecordedWorkspace {
  const base = recordedDemoWorkspace();
  const { status: inlineStatus, ...cleanPatch } = projectPatch;
  const project = { ...base.project, ...cleanPatch } as RecordedWorkspace["project"];
  const episodes = Array.isArray(project.episodes) ? project.episodes : base.project.episodes;
  const mergedStatus = {
    ...base.summary,
    ...(inlineStatus && typeof inlineStatus === "object" ? (inlineStatus as Record<string, unknown>) : {}),
    ...(options.status ?? {}),
  };

  const summaryEpisodes = episodes.map((ep) => {
    const epNum = typeof ep.episode === "number" ? ep.episode : 1;
    const prev = base.summary.episodes.find((entry) => entry.episode === epNum) ?? BASE_EPISODE_SUMMARY;
    const videos =
      ep.videos && typeof ep.videos === "object"
        ? (ep.videos as { total?: number; available?: number })
        : prev.videos;
    const storyboards =
      ep.storyboards && typeof ep.storyboards === "object"
        ? (ep.storyboards as { total?: number; available?: number })
        : prev.storyboards;
    return {
      episode: epNum,
      script_status: typeof ep.script_status === "string" ? ep.script_status : prev.script_status,
      status: typeof ep.status === "string" ? ep.status : prev.status,
      item_count: typeof ep.item_count === "number" ? ep.item_count : prev.item_count,
      duration_seconds: typeof ep.duration_seconds === "number" ? ep.duration_seconds : prev.duration_seconds,
      needs_replan: typeof ep.needs_replan === "number" ? ep.needs_replan : 0,
      videos: { total: videos.total ?? 0, available: videos.available ?? 0 },
      storyboards: { total: storyboards.total ?? 0, available: storyboards.available ?? 0 },
    };
  });

  const episodesSummary =
    (options.status?.episodes_summary as RecordedWorkspace["summary"]["episodes_summary"] | undefined) ??
    (inlineStatus && typeof inlineStatus === "object"
      ? (inlineStatus as { episodes_summary?: RecordedWorkspace["summary"]["episodes_summary"] }).episodes_summary
      : undefined) ?? {
      total: summaryEpisodes.length,
      scripted: summaryEpisodes.filter((entry) => entry.script_status === "generated").length,
      in_production: summaryEpisodes.filter((entry) => entry.status === "in_production").length,
      completed: summaryEpisodes.filter((entry) => entry.status === "completed").length,
    };

  return {
    project,
    summary: {
      ...mergedStatus,
      currency: "registered",
      episodes: summaryEpisodes,
      episodes_summary: episodesSummary,
    },
    asset_fingerprints: {
      ...base.asset_fingerprints,
      ...(options.asset_fingerprints ?? {}),
    },
  };
}

export function workspaceOverride(
  projectPatch: Record<string, unknown> = {},
  options: {
    status?: Record<string, unknown>;
    scripts?: Record<string, Record<string, unknown>>;
    asset_fingerprints?: Record<string, number>;
  } = {},
): ApiOverrides {
  const body = buildWorkspaceBody(projectPatch, options);
  const overrides: ApiOverrides = {
    "GET /api/v1/projects/demo/workspace": {
      status: 200,
      body,
    },
  };
  for (const row of body.summary.episodes) {
    overrides[`GET /api/v1/projects/demo/workspace?episodes=${row.episode}`] = {
      status: 200,
      body: { ...body, summary: { ...body.summary, episodes: [row], episodes_summary: null, assets: {} } },
    };
  }
  if (options.scripts) {
    for (const [file, script] of Object.entries(options.scripts)) {
      const normalized = file.replace(/^scripts\//, "");
      overrides[`GET /api/v1/projects/demo/scripts/${normalized}`] = {
        status: 200,
        body: {
          script,
          revision: BASE_SCRIPT_1.revision,
        },
      };
    }
  }
  return overrides;
}
