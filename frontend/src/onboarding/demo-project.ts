/**
 * 引导演示用的项目数据。
 *
 * 纯前端常量，用真实的 `ProjectSummary` 类型标注 —— 字段随类型演化漂移时会在编译期
 * 暴露，而不是在引导跑到第 3 步时才显形。它只喂给一张只读卡片，不入库、不落盘、不进
 * 项目列表，退出引导即随组件卸载消失。
 *
 * 数字取自一个推进到「制作中」的项目：分集拍完一半、角色差一个、道具还在补。这一步
 * 要讲的是「卡片上这些格子分别代表什么」，所以每一类都取不满的值，让四个统计格都有话说。
 */

import type { TFunction } from "i18next";
import type { ProjectSummary } from "@/types";

/** 演示项目的标识。不对应磁盘上任何项目，仅用于卡片内部的海报配色哈希。 */
export const DEMO_PROJECT_NAME = "onboarding-demo";

export function buildDemoProject(t: TFunction<"onboarding">): ProjectSummary {
  return {
    name: DEMO_PROJECT_NAME,
    title: t("demo_project_title"),
    style: t("demo_project_style"),
    style_template_id: null,
    style_image: null,
    thumbnail: null,
    status: {
      current_phase: "production",
      phase_progress: 0.62,
      characters: { completed: 3, total: 4 },
      scenes: { completed: 5, total: 6 },
      props: { completed: 2, total: 3 },
      episodes_summary: { total: 8, scripted: 3, in_production: 2, completed: 3 },
    },
  };
}
