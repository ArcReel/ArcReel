import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { TFunction } from "i18next";
import { VideoModelSpecBar, videoOptionMetaRenderer, VoiceConsistencyBadge } from "./VideoModelSpecBar";
import type { ModelInfoResponse, ProviderInfo } from "@/types";

describe("VideoModelSpecBar", () => {
  it("renders duration / resolution / audio / voice consistency cells", () => {
    render(<VideoModelSpecBar durations={[4, 6, 8]} resolutions={["720p", "1080p"]} tier="native" />);
    expect(screen.getByText("4, 6, 8s")).toBeInTheDocument();
    expect(screen.getByText("720p / 1080p")).toBeInTheDocument();
    expect(screen.getByText("有声")).toBeInTheDocument();
    expect(screen.getByText("原生一致")).toBeInTheDocument();
  });

  it("shows placeholder dashes when a dimension is unknown", () => {
    render(<VideoModelSpecBar durations={null} resolutions={[]} tier={null} />);
    expect(screen.getAllByText("—").length).toBeGreaterThanOrEqual(4);
  });

  it("renders the silent audio cell text when tier is none", () => {
    render(<VideoModelSpecBar durations={[5]} resolutions={[]} tier="none" />);
    // 音轨格与档位徽章共用「无声」文案，两处均须渲染。
    expect(screen.getAllByText("无声")).toHaveLength(2);
  });
});

// key 回显：断言选中的 key 而非其中文译文，与 task-target.test.ts 同口径。
const t = ((key: string) => key) as unknown as TFunction;

function makeOmniModel(overrides: Partial<ModelInfoResponse> = {}): ModelInfoResponse {
  return {
    display_name: "v3-omni",
    media_type: "video",
    capabilities: ["video"],
    default: false,
    supported_durations: [5],
    duration_resolution_constraints: {},
    resolutions: ["720p"],
    // 可灵 v3-omni：图生可控、参考生恒无声——issue #2022 的取值场景。
    audio_track: "controllable",
    reference_route_audio_track: "always_off",
    voice_consistency: "soft",
    ...overrides,
  };
}

function makeKlingProviders(model: ModelInfoResponse): ProviderInfo[] {
  return [
    {
      id: "kling",
      display_name: "可灵",
      description: "",
      status: "ready",
      media_types: ["video"],
      capabilities: ["video"],
      configured_keys: [],
      missing_keys: [],
      models: { "v3-omni": model },
    },
  ];
}

describe("videoOptionMetaRenderer", () => {
  it("i2v 路线读 audio_track：v3-omni 可控音轨，能力线标有声", () => {
    const providers = makeKlingProviders(makeOmniModel());
    const renderMeta = videoOptionMetaRenderer({ t, providers, customProviders: [], route: "i2v" });
    expect(renderMeta("kling/v3-omni")).toContain("video_spec_audio_has");
  });

  it("r2v 路线读 reference_route_audio_track：同一模型参考生恒无声，能力线标无声", () => {
    const providers = makeKlingProviders(makeOmniModel());
    const renderMeta = videoOptionMetaRenderer({ t, providers, customProviders: [], route: "r2v" });
    expect(renderMeta("kling/v3-omni")).toContain("video_spec_audio_none");
  });

  it("省略 route 时回退目录 i2v 位（全局设置页现行口径），行为与 lookupCatalogVideoAudio 一致", () => {
    const providers = makeKlingProviders(makeOmniModel());
    const renderMeta = videoOptionMetaRenderer({ t, providers, customProviders: [] });
    expect(renderMeta("kling/v3-omni")).toContain("video_spec_audio_has");
  });
});

describe("VoiceConsistencyBadge", () => {
  it("shows the native tier label and its hover description", () => {
    render(<VoiceConsistencyBadge tier="native" />);
    const badge = screen.getByText("原生一致");
    expect(badge.closest("span")).toHaveAttribute("title", expect.stringContaining("不产生额外费用"));
  });

  it("shows the soft tier's non-guarantee wording in the hover description", () => {
    render(<VoiceConsistencyBadge tier="soft" />);
    expect(screen.getByText("软约束").closest("span")).toHaveAttribute(
      "title",
      expect.stringContaining("不保证"),
    );
  });

  it("shows the none tier's unsupported-dialogue wording in the hover description", () => {
    render(<VoiceConsistencyBadge tier="none" />);
    expect(screen.getAllByText("无声")[0].closest("span")).toHaveAttribute(
      "title",
      expect.stringContaining("不支持对白声音"),
    );
  });
});
