import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { TFunction } from "i18next";
import { VideoModelSpecBar, videoOptionMetaRenderer, VoiceConsistencyBadge } from "./VideoModelSpecBar";
import { lookupCatalogVideoAudio } from "@/utils/provider-models";
import type { ModelInfoResponse, ProviderInfo, VideoRoute } from "@/types";

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
    resolutions: ["720p"],
    // 可灵 v3-omni：图生可控、参考生恒无声，两条路径给出不同答案的那一类模型。
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
  const renderer = (defaultRoute?: VideoRoute) =>
    videoOptionMetaRenderer({
      t,
      providers: makeKlingProviders(makeOmniModel()),
      customProviders: [],
      defaultRoute,
    });

  it("i2v 路线读 audio_track：v3-omni 可控音轨，能力线标有声", () => {
    expect(renderer("i2v")("kling/v3-omni")).toContain("video_spec_audio_has");
  });

  it("r2v 路线读 reference_route_audio_track：同一模型参考生恒无声，能力线标无声", () => {
    expect(renderer("r2v")("kling/v3-omni")).toContain("video_spec_audio_none");
  });

  // 无路径上下文（全局设置页的默认模型）时读目录位。这里连带钉住它与 lookupCatalogVideoAudio
  // 同解：目录的 audio_track 就是 i2v 位，两处各算一遍，其中一处改了另一处必须跟着改。
  it("省略 defaultRoute 时按目录位取值，与 lookupCatalogVideoAudio 同解", () => {
    const providers = makeKlingProviders(makeOmniModel());
    const catalog = lookupCatalogVideoAudio(providers, "kling/v3-omni");
    expect(catalog?.hasAudioTrack).toBe(true);
    expect(renderer()("kling/v3-omni")).toContain(
      catalog?.hasAudioTrack ? "video_spec_audio_has" : "video_spec_audio_none",
    );
  });

  // 同屏三个视频下拉共用一个渲染器，细分项自己的路径必须压过默认层的：否则参考生项目里
  // 「图生视频」那一格会按 r2v 标成无声。
  it("细分项 key 压过 defaultRoute：参考生项目里 i2v 桶仍按图生路径标有声", () => {
    expect(renderer("r2v")("kling/v3-omni", "i2v")).toContain("video_spec_audio_has");
  });

  it("细分项 key 压过 defaultRoute：图生项目里 r2v 桶仍按参考生路径标无声", () => {
    expect(renderer("i2v")("kling/v3-omni", "r2v")).toContain("video_spec_audio_none");
  });

  it("图片桶的 key 不是视频路径，取值仍按 defaultRoute 判定", () => {
    // 渲染器由 LayeredModelFields 逐细分项调用，图片桶的 key 同样会传进来；t2i / i2i 不得
    // 被当成视频路径解读。
    expect(renderer("r2v")("kling/v3-omni", "t2i")).toContain("video_spec_audio_none");
    expect(renderer("i2v")("kling/v3-omni", "i2i")).toContain("video_spec_audio_has");
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
