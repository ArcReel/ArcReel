/**
 * Onboarding demo project data.
 *
 * Frontend constants typed with real `ProjectSummary` / `ProjectData` / `EpisodeScript`,
 * exposing evolving-field drift at compilation, not later onboarding steps. Never enter DBs,
 * disk or project lists; inject stores on demo entry, clear on departure.
 *
 * Counts describe in-production projects: episodes half filmed, one character missing, props still filling. Onboarding explains
 * what interface cells mean, so every category remains incomplete, giving statistics something to communicate.
 *
 * Full contents only for episode 1: eight titles, six first-episode storyboards. Copy uses i18n, structure stays here.
 */

import type { TFunction } from "i18next";
import type {
  CameraMotion,
  Character,
  EpisodeMeta,
  NarrationEpisodeScript,
  NarrationSegment,
  Prop,
  ProjectData,
  ProjectStatus,
  ProjectSummary,
  Scene,
  ShotType,
} from "@/types";
import { demoPlaceholder } from "./demo-placeholder";

/**
 * Demo identities contain underscores, always rejected by backend `PROJECT_NAME_PATTERN` (`^[A-Za-z0-9-]+$`),
 * preventing real same-name disk projects from shadowing demo routes.
 */
export const DEMO_PROJECT_NAME = "onboarding_demo";

/** Sole scripted demo episode. */
export const DEMO_SCRIPTED_EPISODE = 1;

/** Script filenames equal `EpisodeMeta.script_file` without `scripts/`. */
const DEMO_SCRIPT_FILE = "E1.json";

// Compare route parameters case-insensitively: wouter compiles `i` flags (APP_PROJECT_WORKSPACE_PATTERN),
// matching variants like `/APP/Projects/ONBOARDING_DEMO`; strict === would miss demos
// and request nonexistent real projects from backends.
export function isDemoProject(name: string | null | undefined): boolean {
  return name?.toLowerCase() === DEMO_PROJECT_NAME;
}

const DEMO_STATUS: ProjectStatus = {
  needs_repair: false,
  repair_reason: null,
  assets: {
    character: { total: 4, available: 3, stale: 0 },
    scene: { total: 3, available: 3, stale: 1 },
    prop: { total: 3, available: 2, stale: 0 },
  },
  episodes_summary: { total: 8, scripted: 1, in_production: 1, completed: 0 },
  source_remaining: false,
};

/**
 * Eight-episode state distribution matches `DEMO_STATUS.episodes_summary`.
 * Only episode 1 has scripts; real backends classify unscripted episodes draft (see
 * `lib/workflow/workflow_state.py::_episode_production_status`), so episodes 2-8 cannot claim
 * scripted/completed while showing placeholders, contradicting statistics.
 */
const EPISODE_STATUSES: NonNullable<EpisodeMeta["status"]>[] = [
  "in_production",
  "draft",
  "draft",
  "draft",
  "draft",
  "draft",
  "draft",
  "draft",
];

/** Six first-episode storyboard skeletons: durations/references/shot types/camera motion; copy indexes into i18n. */
const SEGMENT_SKELETONS: {
  duration: number;
  segmentBreak: boolean;
  characters: number[];
  scenes: number[];
  props: number[];
  shotType: ShotType;
  cameraMotion: CameraMotion;
  hasStoryboard: boolean;
}[] = [
  {
    duration: 5,
    segmentBreak: false,
    characters: [0],
    scenes: [0],
    props: [],
    shotType: "Long Shot",
    cameraMotion: "Pan Right",
    hasStoryboard: true,
  },
  {
    duration: 4,
    segmentBreak: false,
    characters: [0, 1],
    scenes: [0],
    props: [0],
    shotType: "Medium Shot",
    cameraMotion: "Tracking Shot",
    hasStoryboard: true,
  },
  {
    duration: 6,
    segmentBreak: true,
    characters: [0, 1],
    scenes: [0],
    props: [0],
    shotType: "Medium Long Shot",
    cameraMotion: "Push In",
    hasStoryboard: true,
  },
  {
    duration: 7,
    segmentBreak: false,
    characters: [0],
    scenes: [1],
    props: [],
    shotType: "Extreme Long Shot",
    cameraMotion: "Pedestal Down",
    hasStoryboard: true,
  },
  {
    duration: 5,
    segmentBreak: false,
    characters: [0],
    scenes: [1],
    props: [1],
    shotType: "Close-up",
    cameraMotion: "Static",
    hasStoryboard: false,
  },
  {
    duration: 6,
    segmentBreak: true,
    characters: [0],
    scenes: [1],
    props: [1],
    shotType: "Medium Close-up",
    cameraMotion: "Zoom Out",
    hasStoryboard: false,
  },
];

type DemoT = TFunction<"onboarding">;

const characterNames = (t: DemoT): string[] =>
  [1, 2, 3, 4].map((n) => t(`demo_character_${n}_name`));
const sceneNames = (t: DemoT): string[] => [1, 2, 3].map((n) => t(`demo_scene_${n}_name`));
const propNames = (t: DemoT): string[] => [1, 2, 3].map((n) => t(`demo_prop_${n}_name`));

/** Summaries for lobby demo cards. */
export function buildDemoProject(t: DemoT): ProjectSummary {
  return {
    name: DEMO_PROJECT_NAME,
    title: t("demo_project_title"),
    style: t("demo_project_style"),
    style_template_id: null,
    style_image: null,
    thumbnail: null,
    status: DEMO_STATUS,
    // Demo projects lack real modification records; omit Updated days ago.
    last_activity_at: null,
  };
}

/** Complete demo-workbench project shapes. */
export function buildDemoProjectData(t: DemoT): ProjectData {
  const characters: Record<string, Character> = {};
  characterNames(t).forEach((name, i) => {
    characters[name] = {
      description: t(`demo_character_${i + 1}_desc`),
      voice_style: t(`demo_character_${i + 1}_voice`),
      // Fourth characters lack images, matching 3/4 statistics.
      ...(i < 3 ? { character_sheet: demoPlaceholder(name, "character") } : {}),
    };
  });

  const scenes: Record<string, Scene> = {};
  sceneNames(t).forEach((name, i) => {
    scenes[name] = {
      description: t(`demo_scene_${i + 1}_desc`),
      scene_sheet: demoPlaceholder(name, "scene"),
    };
  });

  const props: Record<string, Prop> = {};
  propNames(t).forEach((name, i) => {
    props[name] = {
      description: t(`demo_prop_${i + 1}_desc`),
      // Third props lack images, matching 2/3 statistics.
      ...(i < 2 ? { prop_sheet: demoPlaceholder(name, "prop") } : {}),
    };
  });

  const segmentCount = SEGMENT_SKELETONS.length;
  const episodes: EpisodeMeta[] = EPISODE_STATUSES.map((status, i) => {
    const episode = i + 1;
    const isScripted = episode === DEMO_SCRIPTED_EPISODE;
    return {
      episode,
      title: t(`demo_episode_${episode}_title`),
      // Only first demo episodes have scripts; others have titles only and show Demo covers episode 1 only.
      script_file: isScripted ? `scripts/${DEMO_SCRIPT_FILE}` : "",
      status,
      ...(isScripted
        ? {
            script_status: "generated" as const,
            item_count: segmentCount,
            duration_seconds: SEGMENT_SKELETONS.reduce((sum, s) => sum + s.duration, 0),
            storyboards: {
              total: segmentCount,
              available: SEGMENT_SKELETONS.filter((s) => s.hasStoryboard).length,
              stale: 0,
            },
            // Videos lack usable placeholders (placeholder images replace static images only); demos generate no videos.
            videos: { total: segmentCount, available: 0, stale: 0 },
          }
        : { script_status: "none" as const }),
    };
  });

  return {
    title: t("demo_project_title"),
    content_mode: "narration",
    style: t("demo_project_style"),
    aspect_ratio: "9:16",
    default_duration: 5,
    overview: {
      synopsis: t("demo_overview_synopsis"),
      genre: t("demo_overview_genre"),
      theme: t("demo_overview_theme"),
      world_setting: t("demo_overview_world"),
    },
    episodes,
    characters,
    scenes,
    props,
    status: DEMO_STATUS,
    generation_mode: "storyboard",
  };
}

/** Demo script collections keyed by prefix-stripped `EpisodeMeta.script_file`. */
export function buildDemoScripts(t: DemoT): Record<string, NarrationEpisodeScript> {
  const chars = characterNames(t);
  const scns = sceneNames(t);
  const prps = propNames(t);

  const segments: NarrationSegment[] = SEGMENT_SKELETONS.map((skeleton, i) => {
    const n = i + 1;
    const segmentId = `E${DEMO_SCRIPTED_EPISODE}S${n}`;
    return {
      segment_id: segmentId,
      episode: DEMO_SCRIPTED_EPISODE,
      duration_seconds: skeleton.duration,
      segment_break: skeleton.segmentBreak,
      novel_text: t(`demo_shot_${n}_text`),
      characters_in_segment: skeleton.characters.map((idx) => chars[idx]),
      scenes: skeleton.scenes.map((idx) => scns[idx]),
      props: skeleton.props.map((idx) => prps[idx]),
      image_prompt: {
        scene: t(`demo_shot_${n}_image`),
        composition: {
          shot_type: skeleton.shotType,
          lighting: t(`demo_shot_${n}_lighting`),
          ambiance: t(`demo_shot_${n}_ambiance`),
        },
      },
      video_prompt: {
        action: t(`demo_shot_${n}_video`),
        camera_motion: skeleton.cameraMotion,
        ambiance_audio: t(`demo_shot_${n}_audio`),
        dialogue: [],
      },
      generated_assets: {
        storyboard_image: skeleton.hasStoryboard
          ? demoPlaceholder(segmentId, "storyboard")
          : null,
        storyboard_last_image: null,
        grid_id: null,
        grid_cell_index: null,
        // Videos/speech lack usable placeholders and remain ungenerated in demos.
        video_clip: null,
        video_thumbnail: null,
        video_uri: null,
        narration_audio: null,
        status: skeleton.hasStoryboard ? "storyboard_ready" : "pending",
      },
    };
  });

  return {
    [DEMO_SCRIPT_FILE]: {
      episode: DEMO_SCRIPTED_EPISODE,
      title: t(`demo_episode_${DEMO_SCRIPTED_EPISODE}_title`),
      content_mode: "narration",
      novel: {
        title: t("demo_project_title"),
        chapter: t(`demo_episode_${DEMO_SCRIPTED_EPISODE}_title`),
      },
      segments,
    },
  };
}
