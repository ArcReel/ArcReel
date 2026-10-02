import { describe, expect, it } from "vitest";
import type { TFunction } from "i18next";
import enWorkflow from "@/i18n/en/workflow";
import viWorkflow from "@/i18n/vi/workflow";
import zhWorkflow from "@/i18n/zh/workflow";
import { problemViews } from "./problem-views";

const CATALOGS = { en: enWorkflow, vi: viWorkflow, zh: zhWorkflow } as const;

function translatorFor(catalog: Record<string, string>) {
  return ((key: string, options?: { defaultValue?: string }) =>
    catalog[key] ?? options?.defaultValue ?? key) as unknown as TFunction<"workflow">;
}

const VIDEO_REQUEST_FACTS_CODES = [
  "video_capability_unavailable",
  "video_supported_durations_missing",
  "video_supported_durations_invalid",
  "video_supported_durations_incompatible",
  "reference_capability_unavailable",
  "reference_supported_durations_missing",
  "reference_supported_durations_invalid",
  "reference_supported_durations_incompatible",
];

describe("视频请求事实的问题行", () => {
  it.each(Object.keys(CATALOGS))("%s 下按问题码给出译文，不显示原始码", (locale) => {
    const catalog: Record<string, string> = CATALOGS[locale as keyof typeof CATALOGS];
    const views = problemViews(
      translatorFor(catalog),
      VIDEO_REQUEST_FACTS_CODES.map((code) => ({
        code,
        detail: code,
        action: "configure_provider",
        params: { capability: "video" },
      })),
    );
    const untranslated = views.filter((view, index) => view.summary === VIDEO_REQUEST_FACTS_CODES[index]);
    expect(untranslated).toEqual([]);
  });

  it("模型能力不可用时说明要检查供应商与模型配置", () => {
    const [view] = problemViews(translatorFor(zhWorkflow), [
      {
        code: "video_capability_unavailable",
        detail: "video_capability_unavailable",
        action: "configure_provider",
        params: { capability: "video" },
      },
    ]);
    expect(view.summary).toBe("无法读取所选视频模型的能力，请检查供应商与模型配置。");
    expect(view.detail).toBe("video_capability_unavailable");
  });
});
