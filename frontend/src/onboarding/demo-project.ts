/**
 * 引导演示用的项目数据。
 *
 * 纯前端常量，用真实的 `ProjectSummary` / `ProjectData` / `EpisodeScript` 类型标注 ——
 * 字段随类型演化漂移时会在编译期暴露，而不是在引导跑到某一步时才显形。这份数据不入库、
 * 不落盘、不进项目列表，进演示工作台时灌进 store，离开即随之清空。
 *
 * 数字取自一个推进到「制作中」的项目：分集拍完一半、角色差一个、道具还在补。引导要讲的是
 * 「界面上这些格子分别代表什么」，所以每一类都取不满的值，让统计格都有话说。
 *
 * 内容只完整到第 1 集：8 集都有标题，第 1 集有 6 条分镜。文案全部走 i18n，结构留在这里。
 */

import type { TFunction } from "i18next";
import type {
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
 * 演示项目的标识。含下划线，后端 `PROJECT_NAME_PATTERN`（`^[A-Za-z0-9-]+$`）永远拒绝，
 * 所以磁盘上不可能出现同名真实项目来遮蔽演示路由。
 */
export const DEMO_PROJECT_NAME = "onboarding_demo";

/** 演示里唯一有剧本的分集。 */
export const DEMO_SCRIPTED_EPISODE = 1;

/** 该分集的剧本文件名，与 `EpisodeMeta.script_file` 去掉 `scripts/` 前缀后一致。 */
const DEMO_SCRIPT_FILE = "E1.json";

// 路由参数大小写不敏感比较：wouter 编译路由时带 `i` 标志（见 APP_PROJECT_WORKSPACE_PATTERN），
// `/APP/Projects/ONBOARDING_DEMO` 等大小写变体同样会命中工作台路由，若仅用 === 比较则无法
// 识别成演示项目，转而被当成真实项目名向后端请求一个实际不存在的项目。
export function isDemoProject(name: string | null | undefined): boolean {
  return name?.toLowerCase() === DEMO_PROJECT_NAME;
}

const DEMO_STATUS: ProjectStatus = {
  current_phase: "production",
  phase_progress: 0.62,
  characters: { completed: 3, total: 4 },
  scenes: { completed: 3, total: 3 },
  props: { completed: 2, total: 3 },
  episodes_summary: { total: 8, scripted: 1, in_production: 1, completed: 0 },
};

/**
 * 8 集的状态分布，与 `DEMO_STATUS.episodes_summary` 的计数对齐。
 * 只有第 1 集在演示里带剧本，真实后端对没有剧本的分集只会算出 draft（见
 * `lib/status_calculator.py::_make_fallback_ep_stats`）——第 2-8 集因此不能标成
 * scripted/completed，否则点进去发现只有占位说明，统计与内容对不上。
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

/** 第 1 集 6 条分镜的骨架：时长、出场引用、镜别与运镜。文案按 index 从 i18n 取。 */
const SEGMENT_SKELETONS: {
  duration: number;
  segmentBreak: boolean;
  characters: number[];
  scenes: number[];
  props: number[];
  shotType: ShotType;
  hasStoryboard: boolean;
}[] = [
  {
    duration: 5,
    segmentBreak: false,
    characters: [0],
    scenes: [0],
    props: [],
    shotType: "Long Shot",
    hasStoryboard: true,
  },
  {
    duration: 4,
    segmentBreak: false,
    characters: [0, 1],
    scenes: [0],
    props: [0],
    shotType: "Medium Shot",
    hasStoryboard: true,
  },
  {
    duration: 6,
    segmentBreak: true,
    characters: [0, 1],
    scenes: [0],
    props: [0],
    shotType: "Medium Long Shot",
    hasStoryboard: true,
  },
  {
    duration: 7,
    segmentBreak: false,
    characters: [0],
    scenes: [1],
    props: [],
    shotType: "Extreme Long Shot",
    hasStoryboard: true,
  },
  {
    duration: 5,
    segmentBreak: false,
    characters: [0],
    scenes: [1],
    props: [1],
    shotType: "Close-up",
    hasStoryboard: false,
  },
  {
    duration: 6,
    segmentBreak: true,
    characters: [0],
    scenes: [1],
    props: [1],
    shotType: "Medium Close-up",
    hasStoryboard: false,
  },
];

type DemoT = TFunction<"onboarding">;

const characterNames = (t: DemoT): string[] =>
  [1, 2, 3, 4].map((n) => t(`demo_character_${n}_name`));
const sceneNames = (t: DemoT): string[] => [1, 2, 3].map((n) => t(`demo_scene_${n}_name`));
const propNames = (t: DemoT): string[] => [1, 2, 3].map((n) => t(`demo_prop_${n}_name`));

/** 大厅演示卡用的摘要形态。 */
export function buildDemoProject(t: DemoT): ProjectSummary {
  return {
    name: DEMO_PROJECT_NAME,
    title: t("demo_project_title"),
    style: t("demo_project_style"),
    style_template_id: null,
    style_image: null,
    thumbnail: null,
    status: DEMO_STATUS,
  };
}

/** 演示工作台用的完整项目形态。 */
export function buildDemoProjectData(t: DemoT): ProjectData {
  const characters: Record<string, Character> = {};
  characterNames(t).forEach((name, i) => {
    characters[name] = {
      description: t(`demo_character_${i + 1}_desc`),
      voice_style: t(`demo_character_${i + 1}_voice`),
      // 第 4 个角色还没出图，对应统计格里的 3/4
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
      // 第 3 个道具还没出图，对应统计格里的 2/3
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
      // 只有第 1 集在演示里带剧本；其余分集只有标题，进去会看到「演示只到第 1 集」的说明
      script_file: isScripted ? `scripts/${DEMO_SCRIPT_FILE}` : "",
      status,
      ...(isScripted
        ? {
            scenes_count: segmentCount,
            duration_seconds: SEGMENT_SKELETONS.reduce((sum, s) => sum + s.duration, 0),
            storyboards: {
              total: segmentCount,
              completed: SEGMENT_SKELETONS.filter((s) => s.hasStoryboard).length,
            },
            // 视频没有可用的占位形态（占位图只能替静态图），演示里一条都没出片
            videos: { total: segmentCount, completed: 0 },
          }
        : {}),
    };
  });

  return {
    title: t("demo_project_title"),
    content_mode: "narration",
    source_kind: "novel",
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

/** 演示工作台用的剧本集合，键与 `EpisodeMeta.script_file` 去前缀后对应。 */
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
        ambiance_audio: t(`demo_shot_${n}_audio`),
        dialogue: [],
      },
      transition_to_next: skeleton.segmentBreak ? "fade" : "cut",
      generated_assets: {
        storyboard_image: skeleton.hasStoryboard
          ? demoPlaceholder(segmentId, "storyboard")
          : null,
        storyboard_last_image: null,
        grid_id: null,
        grid_cell_index: null,
        // 视频与配音没有可用的占位形态，演示里保持未生成
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
      duration_seconds: segments.reduce((sum, s) => sum + s.duration_seconds, 0),
      novel: {
        title: t("demo_project_title"),
        chapter: t(`demo_episode_${DEMO_SCRIPTED_EPISODE}_title`),
      },
      segments,
    },
  };
}
