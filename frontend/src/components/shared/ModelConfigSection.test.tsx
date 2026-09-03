import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, it, expect, vi } from "vitest";
import type { ComponentProps } from "react";
import userEvent from "@testing-library/user-event";
import { API, type VideoCapabilitiesQuery } from "@/api";
import { ModelConfigSection } from "./ModelConfigSection";
import type { DurationConstraints, ProviderInfo, VideoCapabilities } from "@/types";
import { lookupSupportedDurations } from "@/utils/provider-models";

const PROVIDERS: ProviderInfo[] = [
  {
    id: "gemini",
    display_name: "Gemini",
    description: "",
    status: "ready",
    media_types: ["video", "image", "text"],
    capabilities: [],
    configured_keys: [],
    missing_keys: [],
    models: {
      "veo-3": {
        display_name: "veo-3",
        media_type: "video",
        capabilities: [],
        default: false,
        supported_durations: [4, 6, 8],
        resolutions: [],
        audio_track: "controllable",
        reference_route_audio_track: "controllable",
        voice_consistency: "soft",
      },
    },
  },
  {
    id: "ark",
    display_name: "Ark",
    description: "",
    status: "ready",
    media_types: ["video"],
    capabilities: [],
    configured_keys: [],
    missing_keys: [],
    models: {
      seedance: {
        display_name: "seedance",
        media_type: "video",
        capabilities: [],
        default: false,
        supported_durations: [5, 8, 10],
        resolutions: [],
        audio_track: "controllable",
        reference_route_audio_track: "controllable",
        voice_consistency: "soft",
      },
    },
  },
];

const OPTIONS = {
  videoBackends: ["gemini/veo-3", "ark/seedance"],
  imageBackends: ["gemini/veo-3"],
  textBackends: ["gemini/veo-3"],
  providerNames: { gemini: "Gemini", ark: "Ark" },
};

const EMPTY_VALUE = {
  videoBackend: "",
  videoProviderI2V: "",
  videoProviderR2V: "",
  imageBackendDefault: "",
  imageBackendT2I: "",
  imageBackendI2I: "",
  textBackendDefault: "",
  textBackendSimple: "",
  textBackendComplex: "",
  defaultDuration: null,
  videoResolution: null,
  imageResolution: null,
} as const;

const EMPTY_GLOBALS = {
  video: "", videoI2V: "", videoR2V: "",
  image: "", imageT2I: "", imageI2I: "",
  textDefault: "", textSimple: "", textComplex: "",
} as const;

// 服务端 video-capabilities 端点的替身：时长全集查当前测试的目录，Veo 的联动约束按上下文给出
// 预设答案（收窄规则本体只在后端 lib/config/resolver.py，这里是固定应答而非复算）。
let fakeCatalog: ProviderInfo[] = PROVIDERS;

function veoConstraints(query: VideoCapabilitiesQuery): DurationConstraints {
  const resolution = query.resolution ?? null;
  const usesReferenceImages = query.usesReferenceImages ?? false;
  const base = { resolution, uses_reference_images: usesReferenceImages };
  if (usesReferenceImages) {
    return {
      ...base,
      allowed: [8],
      allowed_without_reference_images: resolution === "1080p" || resolution === "4k" ? [8] : [4, 6, 8],
      excluded: { "4": "reference", "6": "reference" },
    };
  }
  if (resolution === "1080p" || resolution === "4k") {
    return { ...base, allowed: [8], allowed_without_reference_images: [8], excluded: { "4": "resolution", "6": "resolution" } };
  }
  return { ...base, allowed: [4, 6, 8], allowed_without_reference_images: [4, 6, 8], excluded: {} };
}

function fakeVideoCapabilities(videoBackend: string, query: VideoCapabilitiesQuery): Promise<VideoCapabilities> {
  const durations = lookupSupportedDurations(fakeCatalog, videoBackend);
  if (!durations) return Promise.reject(new Error(`unknown model ${videoBackend}`));
  const [provider_id, model] = videoBackend.split("/");
  const sorted = [...durations].sort((a, b) => a - b);
  const duration_constraints: DurationConstraints =
    videoBackend === "gemini-aistudio/veo"
      ? veoConstraints(query)
      : {
          resolution: query.resolution ?? null,
          uses_reference_images: query.usesReferenceImages ?? false,
          allowed: sorted,
          allowed_without_reference_images: sorted,
          excluded: {},
        };
  return Promise.resolve({
    provider_id,
    model,
    supported_durations: durations,
    max_duration: Math.max(...durations),
    max_reference_images: 3,
    first_frame: true,
    last_frame: true,
    source: "registry",
    voice_consistency: "soft",
    duration_constraints,
  });
}

beforeEach(() => {
  fakeCatalog = PROVIDERS;
  vi.spyOn(API, "getModelVideoCapabilities").mockImplementation((backend, query = {}) =>
    fakeVideoCapabilities(backend, query),
  );
  vi.spyOn(API, "getVideoCapabilities").mockImplementation((_name, query = {}) =>
    fakeVideoCapabilities(query.videoBackend ?? "", query),
  );
});

describe("ModelConfigSection", () => {
  it("renders only the three default-layer selectors when no candidates are supplied", async () => {
    const user = userEvent.setup();
    render(
      <ModelConfigSection
        showSubFields={false}
        value={EMPTY_VALUE}
        onChange={() => {}}
        providers={PROVIDERS}
        options={OPTIONS}
        globalDefaults={{
          ...EMPTY_GLOBALS,
          video: "gemini/veo-3",
          image: "gemini/nano-banana",
          textDefault: "gemini/g25",
          textSimple: "gemini/g25",
          textComplex: "gemini/g25",
        }}
      />,
    );
    // 创建向导路径只剩三个默认层主下拉：video + image + text
    const comboboxes = screen.getAllByRole("combobox");
    expect(comboboxes).toHaveLength(3);
    expect(screen.queryByText("按用途指定模型")).not.toBeInTheDocument();

    // Opening each dropdown should reveal "使用全局默认" as the default option
    await user.click(comboboxes[0]);
    expect(screen.getByRole("option", { name: /使用全局默认/ })).toBeInTheDocument();
    // Close by clicking again
    await user.click(comboboxes[0]);
  });

  it("keeps text tiers when media candidates are unavailable", () => {
    // 候选接口失败时调用方传入 candidates=null；文本档位不取用该数据，不应随之消失
    const { container } = render(
      <ModelConfigSection
        candidates={null}
        value={EMPTY_VALUE}
        onChange={() => {}}
        providers={PROVIDERS}
        options={OPTIONS}
        globalDefaults={{ ...EMPTY_GLOBALS, textDefault: "gemini/g25" }}
      />,
    );
    expect(screen.getByRole("combobox", { name: "简单任务" })).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "复杂任务" })).toBeInTheDocument();
    // 只剩文本这一个折叠区，视频/图片细分因无候选数据而不渲染
    expect(container.querySelectorAll("details")).toHaveLength(1);
  });

  it("keeps configured sub-fields visible and clearable when candidates are unavailable", async () => {
    // 候选拉取失败不应把已保存的覆盖藏起来——它在后端仍生效，藏起来用户既看不见也无法清除
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      <ModelConfigSection
        candidates={null}
        value={{ ...EMPTY_VALUE, imageBackendT2I: "gemini/nano-banana" }}
        onChange={onChange}
        providers={PROVIDERS}
        options={OPTIONS}
        globalDefaults={EMPTY_GLOBALS}
      />,
    );

    // 已配置的「文生图」仍在，且展示的是已保存值；未配置的「图生图」无候选可选，不渲染
    const t2i = screen.getByRole("combobox", { name: "文生图" });
    expect(t2i).toHaveTextContent("nano-banana");
    expect(screen.queryByRole("combobox", { name: "图生图" })).not.toBeInTheDocument();

    // 清空这条覆盖不依赖候选数据
    await user.click(t2i);
    await user.click(screen.getByRole("option", { name: /跟随默认/ }));
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ imageBackendT2I: "" }));
  });

  it("shows an explicit error notice with a retry entry when candidatesError is set, even with no saved overrides", async () => {
    // 候选拉取失败态与「仍在加载中」（candidates=null 但未标记失败）不同：前者要给出可感知的错误信号
    const user = userEvent.setup();
    const onRetry = vi.fn();
    render(
      <ModelConfigSection
        candidates={null}
        candidatesError={{ onRetry }}
        value={EMPTY_VALUE}
        onChange={() => {}}
        providers={PROVIDERS}
        options={OPTIONS}
        globalDefaults={EMPTY_GLOBALS}
      />,
    );
    const alerts = screen.getAllByRole("alert");
    expect(alerts).toHaveLength(2); // video + image；文本档位不取用候选数据，不参与
    for (const alert of alerts) {
      expect(alert).toHaveTextContent(/模型列表加载失败/);
    }
    const retryButtons = screen.getAllByRole("button", { name: "重试" });
    expect(retryButtons).toHaveLength(2);
    await user.click(retryButtons[0]);
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("does not show the error notice when candidates is merely absent without candidatesError", () => {
    render(
      <ModelConfigSection
        candidates={null}
        value={EMPTY_VALUE}
        onChange={() => {}}
        providers={PROVIDERS}
        options={OPTIONS}
        globalDefaults={EMPTY_GLOBALS}
      />,
    );
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  describe("按用途指定模型（项目层）", () => {
    const CANDIDATES = {
      image: {
        default: ["gemini/nano-banana", "openai/gpt-image-edit"],
        buckets: {
          t2i: ["gemini/nano-banana"],
          i2i: ["gemini/nano-banana", "openai/gpt-image-edit"],
        },
      },
      video: {
        default: ["gemini/veo-3", "ark/seedance"],
        buckets: { i2v: ["gemini/veo-3"], r2v: ["ark/seedance"] },
      },
      provider_names: {},
      model_names: {},
    };

    function renderWithCandidates(
      overrides: Partial<ComponentProps<typeof ModelConfigSection>> = {},
    ) {
      return render(
        <ModelConfigSection
          value={EMPTY_VALUE}
          onChange={() => {}}
          providers={PROVIDERS}
          options={OPTIONS}
          candidates={CANDIDATES}
          globalDefaults={EMPTY_GLOBALS}
          {...overrides}
        />,
      );
    }

    it("collapses the sub-fields by default and names each row after its generation path", async () => {
      const user = userEvent.setup();
      const { container } = renderWithCandidates();
      // 三个媒体各一个折叠区，初始收起
      const sections = Array.from(container.querySelectorAll("details"));
      expect(sections).toHaveLength(3);
      expect(sections.every((d) => !d.open)).toBe(true);

      for (const summary of screen.getAllByText("按用途指定模型")) {
        await user.click(summary);
      }
      for (const name of ["图生视频", "参考生视频", "文生图", "图生图", "简单任务", "复杂任务"]) {
        expect(screen.getByRole("combobox", { name })).toBeInTheDocument();
      }
      // 界面文案不出现内部术语
      expect(container).not.toHaveTextContent(/能力桶|任务类型桶|capability bucket/i);
    });

    it("feeds each sub-field from its own filtered candidate list while the default layer stays unfiltered", async () => {
      const user = userEvent.setup();
      renderWithCandidates();
      await user.click(screen.getAllByText("按用途指定模型")[0]);

      // 默认层不过滤：两个视频模型都在
      await user.click(screen.getByRole("combobox", { name: "默认视频模型" }));
      expect(screen.getByRole("option", { name: /veo-3/ })).toBeInTheDocument();
      expect(screen.getByRole("option", { name: /seedance/ })).toBeInTheDocument();
      await user.keyboard("{Escape}");

      // 图生视频桶只列 i2v 候选
      await user.click(screen.getByRole("combobox", { name: "图生视频" }));
      expect(screen.getByRole("option", { name: /veo-3/ })).toBeInTheDocument();
      expect(screen.queryByRole("option", { name: /seedance/ })).not.toBeInTheDocument();
    });

    it("lets the project default model win over every global layer in the placeholder", async () => {
      const user = userEvent.setup();
      renderWithCandidates({
        value: { ...EMPTY_VALUE, videoBackend: "gemini/veo-3" },
        globalDefaults: { ...EMPTY_GLOBALS, video: "ark/seedance", videoR2V: "ark/seedance" },
      });
      await user.click(screen.getAllByText("按用途指定模型")[0]);
      expect(screen.getByRole("combobox", { name: "图生视频" })).toHaveTextContent(
        /跟随默认 · Gemini · veo-3/,
      );
    });

    it("falls through to the global bucket, then the global default, when the project layer is empty", async () => {
      const user = userEvent.setup();
      renderWithCandidates({
        globalDefaults: { ...EMPTY_GLOBALS, video: "gemini/veo-3", videoR2V: "ark/seedance" },
      });
      await user.click(screen.getAllByText("按用途指定模型")[0]);
      // r2v 有全局桶 → 用桶值；i2v 无 → 落到全局默认模型
      expect(screen.getByRole("combobox", { name: "参考生视频" })).toHaveTextContent(
        /跟随默认 · Ark · seedance/,
      );
      expect(screen.getByRole("combobox", { name: "图生视频" })).toHaveTextContent(
        /跟随默认 · Gemini · veo-3/,
      );
    });

    it("revalidates duration and resolution when a sub-field switches the executing model", async () => {
      // 细分项改动同样换掉执行模型，分辨率没有越界提示兜底，必须在此清掉
      const user = userEvent.setup();
      const onChange = vi.fn();
      renderWithCandidates({
        value: {
          ...EMPTY_VALUE,
          videoBackend: "gemini/veo-3",
          defaultDuration: 4,
          videoResolution: "1080p",
        },
        onChange,
        // i2v 桶另放一个模型，才能走到「执行桶换模型」这条分支
        candidates: {
          ...CANDIDATES,
          video: { ...CANDIDATES.video, buckets: { i2v: ["gemini/veo-3", "ark/seedance"], r2v: ["ark/seedance"] } },
        },
      });
      await user.click(screen.getAllByText("按用途指定模型")[0]);
      await user.click(screen.getByRole("combobox", { name: "参考生视频" }));
      await user.click(screen.getByRole("option", { name: /seedance/ }));
      expect(onChange).toHaveBeenCalledWith(
        expect.objectContaining({
          videoProviderR2V: "ark/seedance",
          // 项目走图生视频路径，r2v 不是执行桶——执行模型没变，两者原样保留
          defaultDuration: 4,
          videoResolution: "1080p",
        }),
      );

      // 换的是执行桶：分辨率清空，4 秒不在 seedance 的支持集里，时长退回自动
      onChange.mockClear();
      await user.click(screen.getByRole("combobox", { name: "图生视频" }));
      await user.click(screen.getByRole("option", { name: /seedance/ }));
      expect(onChange).toHaveBeenCalledWith(
        expect.objectContaining({
          videoProviderI2V: "ark/seedance",
          defaultDuration: null,
          videoResolution: null,
        }),
      );
    });

    it("clears the resolution when a sub-field change moves the executing image model", async () => {
      const user = userEvent.setup();
      const onChange = vi.fn();
      renderWithCandidates({
        value: { ...EMPTY_VALUE, imageBackendDefault: "openai/gpt-image-edit", imageResolution: "1080p" },
        onChange,
      });
      await user.click(screen.getAllByText("按用途指定模型")[1]);
      await user.click(screen.getByRole("combobox", { name: "文生图" }));
      await user.click(screen.getByRole("option", { name: /nano-banana/ }));
      expect(onChange).toHaveBeenCalledWith(
        expect.objectContaining({ imageBackendT2I: "gemini/nano-banana", imageResolution: null }),
      );
    });

    it("auto-expands and counts sub-fields that already carry a value", () => {
      const { container } = renderWithCandidates({
        value: { ...EMPTY_VALUE, videoProviderR2V: "ark/seedance" },
      });
      const videoSection = container.querySelector("details");
      expect(videoSection?.open).toBe(true);
      expect(screen.getByText("已指定 1 项")).toBeInTheDocument();
    });
  });

  it("renders duration buttons based on supported_durations of current video backend", async () => {
    const { rerender } = render(
      <ModelConfigSection
        value={{ ...EMPTY_VALUE, videoBackend: "gemini/veo-3" }}
        onChange={() => {}}
        providers={PROVIDERS}
        options={OPTIONS}
        globalDefaults={EMPTY_GLOBALS}
      />,
    );
    expect(await screen.findByRole("radio", { name: "4 秒" })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "6 秒" })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "8 秒" })).toBeInTheDocument();
    expect(screen.queryByRole("radio", { name: "5 秒" })).not.toBeInTheDocument();

    rerender(
      <ModelConfigSection
        value={{ ...EMPTY_VALUE, videoBackend: "ark/seedance" }}
        onChange={() => {}}
        providers={PROVIDERS}
        options={OPTIONS}
        globalDefaults={EMPTY_GLOBALS}
      />,
    );
    expect(await screen.findByRole("radio", { name: "5 秒" })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "8 秒" })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "10 秒" })).toBeInTheDocument();
    expect(screen.queryByRole("radio", { name: "4 秒" })).not.toBeInTheDocument();
  });

  it("derives duration options from the bucket that the project actually executes", async () => {
    // 默认层 veo-3（4/6/8），但图生视频桶覆盖成 seedance（5/8/10）——执行的是后者，
    // 按默认层列时长会让用户存下执行时被拒的取值
    const { rerender } = render(
      <ModelConfigSection
        value={{ ...EMPTY_VALUE, videoBackend: "gemini/veo-3", videoProviderI2V: "ark/seedance" }}
        onChange={() => {}}
        providers={PROVIDERS}
        options={OPTIONS}
        globalDefaults={EMPTY_GLOBALS}
      />,
    );
    expect(await screen.findByRole("radio", { name: "10 秒" })).toBeInTheDocument();
    expect(screen.queryByRole("radio", { name: "4 秒" })).not.toBeInTheDocument();

    // 参考生视频项目改走 r2v 桶，i2v 的覆盖对它不作数
    rerender(
      <ModelConfigSection
        usesReferenceImages
        value={{ ...EMPTY_VALUE, videoBackend: "gemini/veo-3", videoProviderI2V: "ark/seedance" }}
        onChange={() => {}}
        providers={PROVIDERS}
        options={OPTIONS}
        globalDefaults={EMPTY_GLOBALS}
      />,
    );
    expect(await screen.findByRole("radio", { name: "4 秒" })).toBeInTheDocument();
    expect(screen.queryByRole("radio", { name: "10 秒" })).not.toBeInTheDocument();
  });

  it("keeps duration and resolution when the default layer changes but the bucket still wins", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      <ModelConfigSection
        value={{
          ...EMPTY_VALUE,
          videoBackend: "gemini/veo-3",
          videoProviderI2V: "ark/seedance",
          defaultDuration: 10,
          videoResolution: "1080p",
        }}
        onChange={onChange}
        providers={PROVIDERS}
        options={OPTIONS}
        globalDefaults={EMPTY_GLOBALS}
      />,
    );
    await user.click(screen.getByRole("combobox", { name: "默认视频模型" }));
    await user.click(screen.getByRole("option", { name: /seedance/ }));
    // 执行模型没变（桶仍指向 seedance），时长与分辨率不该被重置
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({ videoBackend: "ark/seedance", defaultDuration: 10, videoResolution: "1080p" }),
    );
  });

  it("resets defaultDuration to null when video backend change drops current duration", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      <ModelConfigSection
        value={{ ...EMPTY_VALUE, videoBackend: "gemini/veo-3", defaultDuration: 4 }}
        onChange={onChange}
        providers={PROVIDERS}
        options={OPTIONS}
        globalDefaults={EMPTY_GLOBALS}
      />,
    );
    // Open the video backend dropdown
    const videoTrigger = screen.getByRole("combobox", { name: /视频模型/ });
    await user.click(videoTrigger);
    // Click on the ark/seedance option (4s is not in its supported_durations: [5, 8, 10])
    const seedanceOption = screen.getByRole("option", { name: /seedance/ });
    await user.click(seedanceOption);

    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({
        videoBackend: "ark/seedance",
        defaultDuration: null,
      }),
    );
  });

  it("preserves defaultDuration when new video backend still supports it", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      <ModelConfigSection
        value={{ ...EMPTY_VALUE, videoBackend: "gemini/veo-3", defaultDuration: 8 }}
        onChange={onChange}
        providers={PROVIDERS}
        options={OPTIONS}
        globalDefaults={EMPTY_GLOBALS}
      />,
    );
    const videoTrigger = screen.getByRole("combobox", { name: /视频模型/ });
    await user.click(videoTrigger);
    const seedanceOption = screen.getByRole("option", { name: /seedance/ });
    await user.click(seedanceOption);

    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({
        videoBackend: "ark/seedance",
        defaultDuration: 8, // 8 is in both supported lists
      }),
    );
  });

  it("renders the spec bar with a catalog-derived voice consistency tier when no project context is given", () => {
    render(
      <ModelConfigSection
        value={{ ...EMPTY_VALUE, videoBackend: "gemini/veo-3" }}
        onChange={() => {}}
        providers={PROVIDERS}
        options={OPTIONS}
        globalDefaults={EMPTY_GLOBALS}
      />,
    );
    // 无 projectName（全局设置场景）：档位直接取目录端点的服务端派生值，前端不再自行推导。
    expect(screen.getByText("有声")).toBeInTheDocument();
    expect(screen.getByText("软约束")).toBeInTheDocument();
  });

  it("shows a duration/audio capability line under each option in the video dropdown", async () => {
    const user = userEvent.setup();
    render(
      <ModelConfigSection
        value={EMPTY_VALUE}
        onChange={() => {}}
        providers={PROVIDERS}
        options={OPTIONS}
        globalDefaults={EMPTY_GLOBALS}
      />,
    );
    const videoTrigger = screen.getByRole("combobox", { name: /视频模型/ });
    await user.click(videoTrigger);
    expect(screen.getByText("5, 8, 10s · 有声")).toBeInTheDocument();
  });

  it("respects enable.video=false to hide the video card", () => {
    render(
      <ModelConfigSection
        value={EMPTY_VALUE}
        onChange={() => {}}
        providers={PROVIDERS}
        options={OPTIONS}
        globalDefaults={EMPTY_GLOBALS}
        enable={{ video: false }}
      />,
    );
    // No combobox for video model should be visible
    expect(screen.queryByRole("combobox", { name: /视频模型/ })).not.toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: /^默认图片模型$/ })).toBeInTheDocument();
  });

  it("falls back to globalDefaults.video supported_durations when videoBackend is empty (bug repro)", async () => {
    render(
      <ModelConfigSection
        value={EMPTY_VALUE}
        onChange={() => {}}
        providers={PROVIDERS}
        options={OPTIONS}
        globalDefaults={{ ...EMPTY_GLOBALS, video: "ark/seedance" }}
      />,
    );
    // Should reflect ark/seedance's supported_durations [5, 8, 10]
    expect(await screen.findByRole("radio", { name: "5 秒" })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "8 秒" })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "10 秒" })).toBeInTheDocument();
    // Should NOT show DEFAULT_DURATIONS buttons that ark/seedance doesn't support
    expect(screen.queryByRole("radio", { name: "4 秒" })).not.toBeInTheDocument();
    expect(screen.queryByRole("radio", { name: "6 秒" })).not.toBeInTheDocument();
  });

  it("hides duration picker when videoBackend is empty and no global default", () => {
    render(
      <ModelConfigSection
        value={EMPTY_VALUE}
        onChange={() => {}}
        providers={PROVIDERS}
        options={OPTIONS}
        globalDefaults={EMPTY_GLOBALS}
      />,
    );
    // 不再 fallback 到 [4,6,8] —— 整个时长卡片不渲染；没有候选模型也就没有可查的能力，不发请求
    expect(screen.queryByRole("radio", { name: "4 秒" })).not.toBeInTheDocument();
    expect(screen.queryByRole("radio", { name: "6 秒" })).not.toBeInTheDocument();
    expect(screen.queryByRole("radio", { name: "8 秒" })).not.toBeInTheDocument();
    expect(API.getModelVideoCapabilities).not.toHaveBeenCalled();
  });

  it("renders slider when supported_durations is continuous integer range ≥ 5", async () => {
    const continuousProviders: ProviderInfo[] = [
      {
        id: "ark",
        display_name: "Ark",
        description: "",
        status: "ready",
        media_types: ["video"],
        capabilities: [],
        configured_keys: [],
        missing_keys: [],
        models: {
          seedance: {
            display_name: "seedance",
            media_type: "video",
            capabilities: [],
            default: false,
            supported_durations: [3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15],
            resolutions: [],
            audio_track: "controllable",
            reference_route_audio_track: "controllable",
            voice_consistency: "soft",
          },
        },
      },
    ];
    fakeCatalog = continuousProviders;
    render(
      <ModelConfigSection
        value={{ ...EMPTY_VALUE, videoBackend: "ark/seedance" }}
        onChange={() => {}}
        providers={continuousProviders}
        options={{ ...OPTIONS, videoBackends: ["ark/seedance"] }}
        globalDefaults={EMPTY_GLOBALS}
      />,
    );
    // 连续区间 → slider，不再有按钮组（除 auto + slider 自身的 radio）
    expect(await screen.findByRole("slider")).toBeInTheDocument();
    expect(screen.queryByRole("radio", { name: "3 秒" })).not.toBeInTheDocument();
  });

  it("hides duration picker when effective backend has no supported_durations", async () => {
    render(
      <ModelConfigSection
        value={{ ...EMPTY_VALUE, videoBackend: "unknown/no-such" }}
        onChange={() => {}}
        providers={PROVIDERS}
        options={{ ...OPTIONS, videoBackends: ["unknown/no-such"] }}
        globalDefaults={EMPTY_GLOBALS}
      />,
    );
    await waitFor(() => expect(API.getModelVideoCapabilities).toHaveBeenCalled());
    expect(screen.queryByRole("slider")).not.toBeInTheDocument();
    expect(screen.queryByRole("radio", { name: /^\d+ 秒$/ })).not.toBeInTheDocument();
  });

  it("marks 'auto' radio as checked when defaultDuration is null", async () => {
    render(
      <ModelConfigSection
        value={{ ...EMPTY_VALUE, videoBackend: "gemini/veo-3", defaultDuration: null }}
        onChange={() => {}}
        providers={PROVIDERS}
        options={OPTIONS}
        globalDefaults={EMPTY_GLOBALS}
      />,
    );
    expect(await screen.findByRole("radio", { name: "auto" })).toBeChecked();
  });

  it("marks the selected duration radio as checked", async () => {
    render(
      <ModelConfigSection
        value={{ ...EMPTY_VALUE, videoBackend: "gemini/veo-3", defaultDuration: 6 }}
        onChange={() => {}}
        providers={PROVIDERS}
        options={OPTIONS}
        globalDefaults={EMPTY_GLOBALS}
      />,
    );
    expect(await screen.findByRole("radio", { name: "6 秒" })).toBeChecked();
    expect(screen.getByRole("radio", { name: "4 秒" })).not.toBeChecked();
  });

  it("calls onChange with updated defaultDuration when duration button clicked", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      <ModelConfigSection
        value={{ ...EMPTY_VALUE, videoBackend: "gemini/veo-3", defaultDuration: null }}
        onChange={onChange}
        providers={PROVIDERS}
        options={OPTIONS}
        globalDefaults={EMPTY_GLOBALS}
      />,
    );
    await user.click(await screen.findByRole("radio", { name: "6 秒" }));
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ defaultDuration: 6 }));
  });

  it("shows an out-of-range notice with no duration radio checked when saved duration is unsupported", async () => {
    render(
      <ModelConfigSection
        value={{ ...EMPTY_VALUE, videoBackend: "gemini/veo-3", defaultDuration: 10 }}
        onChange={() => {}}
        providers={PROVIDERS}
        options={OPTIONS}
        globalDefaults={EMPTY_GLOBALS}
      />,
    );
    // 越界提示含失效秒数（10 不在 gemini/veo-3 的 [4,6,8] 内）
    expect(await screen.findByText(/不再受当前模型支持/)).toBeInTheDocument();
    expect(screen.getByText(/10/)).toBeInTheDocument();
    // 无任何时长钮处于激活态：auto 与所有数字钮 aria-checked 均为 false
    expect(screen.getByRole("radio", { name: "auto" })).not.toBeChecked();
    for (const sec of ["4 秒", "6 秒", "8 秒"]) {
      expect(screen.getByRole("radio", { name: sec })).not.toBeChecked();
    }
    // 越界态下 auto 兜底为可聚焦入口，键盘仍能 Tab 进 radiogroup 重选（无元素 tabIndex=0 会成键盘陷阱）
    expect(screen.getByRole("radio", { name: "auto" })).toHaveAttribute("tabindex", "0");
  });

  it("resets defaultDuration to null when the out-of-range reset action is clicked", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      <ModelConfigSection
        value={{ ...EMPTY_VALUE, videoBackend: "gemini/veo-3", defaultDuration: 10 }}
        onChange={onChange}
        providers={PROVIDERS}
        options={OPTIONS}
        globalDefaults={EMPTY_GLOBALS}
      />,
    );
    await user.click(await screen.findByRole("button", { name: "回退到 auto" }));
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ defaultDuration: null }));
  });

  it("does not show the out-of-range notice when saved duration is supported", async () => {
    render(
      <ModelConfigSection
        value={{ ...EMPTY_VALUE, videoBackend: "gemini/veo-3", defaultDuration: 6 }}
        onChange={() => {}}
        providers={PROVIDERS}
        options={OPTIONS}
        globalDefaults={EMPTY_GLOBALS}
      />,
    );
    await screen.findByRole("radio", { name: "6 秒" });
    expect(screen.queryByText(/不再受当前模型支持/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "回退到 auto" })).not.toBeInTheDocument();
  });

  it("shows the out-of-range notice and reset action under the slider branch too", async () => {
    const continuousProviders: ProviderInfo[] = [
      {
        id: "ark",
        display_name: "Ark",
        description: "",
        status: "ready",
        media_types: ["video"],
        capabilities: [],
        configured_keys: [],
        missing_keys: [],
        models: {
          seedance: {
            display_name: "seedance",
            media_type: "video",
            capabilities: [],
            default: false,
            supported_durations: [3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15],
            resolutions: [],
            audio_track: "controllable",
            reference_route_audio_track: "controllable",
            voice_consistency: "soft",
          },
        },
      },
    ];
    const user = userEvent.setup();
    const onChange = vi.fn();
    fakeCatalog = continuousProviders;
    render(
      <ModelConfigSection
        value={{ ...EMPTY_VALUE, videoBackend: "ark/seedance", defaultDuration: 20 }}
        onChange={onChange}
        providers={continuousProviders}
        options={{ ...OPTIONS, videoBackends: ["ark/seedance"] }}
        globalDefaults={EMPTY_GLOBALS}
      />,
    );
    // slider 分支：20 不在 [3..15] 内
    const slider = await screen.findByRole("slider");
    expect(slider).toBeInTheDocument();
    expect(screen.getByText(/不再受当前模型支持/)).toBeInTheDocument();
    // 越界值的读数/aria-valuetext 忠实显示原值，而非误报为 auto——与未激活的 auto 钮及
    // 点名秒数的越界提示一致
    expect(slider).toHaveAttribute("aria-valuetext", expect.stringMatching(/20/));
    expect(slider.getAttribute("aria-valuetext")).not.toBe("auto");
    await user.click(screen.getByRole("button", { name: "回退到 auto" }));
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ defaultDuration: null }));
  });

  // ── 联动约束：分辨率 / 参考图路径收窄可选时长 ──────────────────

  const VEO_PROVIDERS: ProviderInfo[] = [
    {
      id: "gemini-aistudio",
      display_name: "AI Studio",
      description: "",
      status: "ready",
      media_types: ["video"],
      capabilities: [],
      configured_keys: [],
      missing_keys: [],
      models: {
        veo: {
          display_name: "Veo 3.1",
          media_type: "video",
          capabilities: [],
          default: false,
          supported_durations: [4, 6, 8],
          resolutions: ["720p", "1080p", "4k"],
          audio_track: "controllable",
          reference_route_audio_track: "controllable",
          voice_consistency: "soft",
        },
      },
    },
  ];

  const VEO_OPTIONS = {
    videoBackends: ["gemini-aistudio/veo"],
    imageBackends: [],
    textBackends: [],
    providerNames: { "gemini-aistudio": "AI Studio" },
  };

  const NO_GLOBAL_DEFAULTS = EMPTY_GLOBALS;

  function renderVeo(
    overrides: Partial<React.ComponentProps<typeof ModelConfigSection>> & {
      videoResolution?: string | null;
      defaultDuration?: number | null;
    } = {},
  ) {
    const { videoResolution = null, defaultDuration = null, ...props } = overrides;
    fakeCatalog = VEO_PROVIDERS;
    return render(
      <ModelConfigSection
        value={{
          ...EMPTY_VALUE,
          videoBackend: "gemini-aistudio/veo",
          videoResolution,
          defaultDuration,
        }}
        onChange={() => {}}
        providers={VEO_PROVIDERS}
        options={VEO_OPTIONS}
        globalDefaults={NO_GLOBAL_DEFAULTS}
        {...props}
      />,
    );
  }

  it("offers every supported duration at an unconstrained resolution", async () => {
    renderVeo({ videoResolution: "720p" });
    await screen.findByRole("radio", { name: "4 秒" });
    for (const sec of ["4 秒", "6 秒", "8 秒"]) {
      expect(screen.getByRole("radio", { name: sec })).toBeInTheDocument();
    }
  });

  it.each(["1080p", "4k"])("offers only 8s at %s", async (resolution) => {
    renderVeo({ videoResolution: resolution });
    expect(await screen.findByRole("radio", { name: "8 秒" })).toBeInTheDocument();
    expect(screen.queryByRole("radio", { name: "4 秒" })).not.toBeInTheDocument();
    expect(screen.queryByRole("radio", { name: "6 秒" })).not.toBeInTheDocument();
    // 表单里未保存的分辨率显式交给服务端求值，而不是等保存后再算
    expect(API.getModelVideoCapabilities).toHaveBeenLastCalledWith(
      "gemini-aistudio/veo",
      expect.objectContaining({ resolution }),
    );
  });

  it("offers only 8s on the reference-video path even at 720p", async () => {
    renderVeo({ videoResolution: "720p", usesReferenceImages: true });
    expect(await screen.findByRole("radio", { name: "8 秒" })).toBeInTheDocument();
    expect(screen.queryByRole("radio", { name: "4 秒" })).not.toBeInTheDocument();
    expect(API.getModelVideoCapabilities).toHaveBeenLastCalledWith(
      "gemini-aistudio/veo",
      expect.objectContaining({ resolution: "720p", usesReferenceImages: true }),
    );
  });

  it("sends the project name and an explicit auto resolution when editing a saved project", async () => {
    renderVeo({ projectName: "saved-project", videoResolution: null });
    await screen.findByRole("radio", { name: "4 秒" });
    // 表单里的「自动」显式传 null：服务端不得回退到该项目已保存的档位来收窄
    expect(API.getVideoCapabilities).toHaveBeenLastCalledWith(
      "saved-project",
      expect.objectContaining({ videoBackend: "gemini-aistudio/veo", resolution: null }),
    );
    expect(API.getModelVideoCapabilities).not.toHaveBeenCalled();
  });

  // 警告文案按越界成因分开：模型本身仍支持 4 秒，指向「模型不支持」会把用户引去换模型。
  it.each([
    ["1080p 分辨率", { videoResolution: "1080p" }, /当前分辨率下不可用/],
    ["参考生视频", { videoResolution: "720p", usesReferenceImages: true }, /参考生视频下不可用/],
  ])("warns about a saved 4s duration under %s", async (_label, overrides, expected) => {
    renderVeo({ ...overrides, defaultDuration: 4 });
    expect(await screen.findByRole("alert")).toHaveTextContent(expected);
    expect(screen.getByRole("alert")).not.toHaveTextContent(/不再受当前模型支持/);
    expect(screen.getByRole("radio", { name: "8 秒" })).not.toBeChecked();
  });

  it("keeps a saved 4s duration valid when neither constraint applies", async () => {
    renderVeo({ videoResolution: "720p", defaultDuration: 4 });
    expect(await screen.findByRole("radio", { name: "4 秒" })).toBeChecked();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});

describe("音频开关的模型可控性", () => {
  const AUDIO_PROVIDERS: ProviderInfo[] = [
    {
      id: "ark",
      display_name: "Ark",
      description: "",
      status: "ready",
      media_types: ["video"],
      capabilities: [],
      configured_keys: [],
      missing_keys: [],
      models: {
        seedance: {
          display_name: "seedance",
          media_type: "video",
          capabilities: [],
          default: false,
          supported_durations: [5],
          resolutions: [],
          audio_track: "controllable",
          reference_route_audio_track: "controllable",
          voice_consistency: "soft",
        },
      },
    },
    {
      // 可灵 v3-omni 的形状：图生子路径带音轨开关，参考生子路径的原生 schema 不含该字段。
      id: "kling",
      display_name: "Kling",
      description: "",
      status: "ready",
      media_types: ["video"],
      capabilities: [],
      configured_keys: [],
      missing_keys: [],
      models: {
        "v3-omni": {
          display_name: "v3-omni",
          media_type: "video",
          capabilities: [],
          default: false,
          supported_durations: [5],
          resolutions: [],
          audio_track: "controllable",
          reference_route_audio_track: "always_off",
          voice_consistency: "soft",
        },
      },
    },
    {
      id: "dashscope",
      display_name: "DashScope",
      description: "",
      status: "ready",
      media_types: ["video"],
      capabilities: [],
      configured_keys: [],
      missing_keys: [],
      models: {
        wan: {
          display_name: "wan",
          media_type: "video",
          capabilities: [],
          default: false,
          supported_durations: [5],
          resolutions: [],
          audio_track: "always_on",
          reference_route_audio_track: "always_on",
          voice_consistency: "soft",
        },
      },
    },
    {
      id: "minimax",
      display_name: "MiniMax",
      description: "",
      status: "ready",
      media_types: ["video"],
      capabilities: [],
      configured_keys: [],
      missing_keys: [],
      models: {
        "hailuo-02": {
          display_name: "hailuo-02",
          media_type: "video",
          capabilities: [],
          default: false,
          supported_durations: [6],
          resolutions: [],
          audio_track: "always_off",
          reference_route_audio_track: "always_off",
          voice_consistency: "none",
        },
      },
    },
  ];

  function renderAudio(
    videoBackend: string,
    videoGenerateAudio: boolean | null,
    onVideoGenerateAudioChange = vi.fn(),
    globalVideoGenerateAudio = true,
    usesReferenceImages = false,
  ) {
    render(
      <ModelConfigSection
        showSubFields={false}
        value={{ ...EMPTY_VALUE, videoBackend }}
        onChange={() => {}}
        providers={AUDIO_PROVIDERS}
        usesReferenceImages={usesReferenceImages}
        options={{
          videoBackends: ["ark/seedance", "dashscope/wan", "minimax/hailuo-02", "kling/v3-omni"],
          imageBackends: [],
          textBackends: [],
          providerNames: {},
        }}
        globalDefaults={EMPTY_GLOBALS}
        videoGenerateAudio={videoGenerateAudio}
        globalVideoGenerateAudio={globalVideoGenerateAudio}
        onVideoGenerateAudioChange={onVideoGenerateAudioChange}
        enable={{ image: false, text: false }}
      />,
    );
    return onVideoGenerateAudioChange;
  }

  it("keeps the switch interactive for a model whose audio track is controllable", async () => {
    const user = userEvent.setup();
    const onChange = renderAudio("ark/seedance", null);
    const off = screen.getByRole("radio", { name: "关闭" });
    expect(off).toBeEnabled();
    await user.click(off);
    expect(onChange).toHaveBeenCalledWith(false);
    expect(screen.queryByText(/音频开关不可调整/)).not.toBeInTheDocument();
  });

  it("locks the switch on an always-audible model and shows the film is audible", () => {
    renderAudio("dashscope/wan", null);
    expect(screen.getByRole("radio", { name: "关闭" })).toBeDisabled();
    expect(screen.getByRole("radio", { name: "开启" })).toBeChecked();
    expect(screen.getByText(/始终带声音/)).toBeInTheDocument();
  });

  it("locks the switch on a model without an audio track and shows the film is silent", () => {
    renderAudio("minimax/hailuo-02", null);
    expect(screen.getByRole("radio", { name: "开启" })).toBeDisabled();
    expect(screen.getByRole("radio", { name: "关闭" })).toBeChecked();
    expect(screen.getByText(/没有声音/)).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  // 存量配置存了「关闭」而模型关不掉：置灰后仍须留一条修正路径，否则设置改不回来。
  it("offers a one-click fix when a stored off setting contradicts an always-audible model", async () => {
    const user = userEvent.setup();
    const onChange = renderAudio("dashscope/wan", false);
    expect(screen.getByRole("alert")).toHaveTextContent(/无法关闭声音/);
    await user.click(screen.getByRole("button", { name: "改为开启" }));
    expect(onChange).toHaveBeenCalledWith(true);
  });

  // 项目级留空是「跟随全局」：全局为关闭时同样落在恒有声模型上，提示按生效值给，
  // 否则界面只显示置灰的「开启」，用户要到入队被拒才知道配置有矛盾。
  it("offers the same fix when the project follows a global off setting", async () => {
    const user = userEvent.setup();
    const onChange = renderAudio("dashscope/wan", null, vi.fn(), false);
    expect(screen.getByRole("alert")).toHaveTextContent(/无法关闭声音/);
    await user.click(screen.getByRole("button", { name: "改为开启" }));
    expect(onChange).toHaveBeenCalledWith(true);
  });

  it("stays quiet when the project follows a global on setting", () => {
    renderAudio("dashscope/wan", null, vi.fn(), true);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  // 音轨形态按执行路径分叉：同一模型在图生路线可控、在参考生视频没有开关可下发。按模型（而非
  // 按路径）取值会让参考生视频的用户开着音频却拿到无声成片，且全程无提示。
  it("keeps the switch interactive for a route-split model on the first-frame route", () => {
    renderAudio("kling/v3-omni", null);
    expect(screen.getByRole("radio", { name: "关闭" })).toBeEnabled();
    expect(screen.queryByText(/没有声音/)).not.toBeInTheDocument();
  });

  it("locks the switch for the same model on the reference route and shows the film is silent", () => {
    renderAudio("kling/v3-omni", null, vi.fn(), true, true);
    expect(screen.getByRole("radio", { name: "开启" })).toBeDisabled();
    expect(screen.getByRole("radio", { name: "关闭" })).toBeChecked();
    expect(screen.getByText(/没有声音/)).toBeInTheDocument();
  });

  // 同屏三个视频下拉分属三条不同路径：默认层跟着项目实际执行的那条，两个细分项各按自己的桶。
  // 音轨格与下方开关必须给出同一个答案——一屏之内两句话互相矛盾，用户按下拉预览选型就会选错。
  describe("候选下拉的音轨能力线", () => {
    const OMNI_CANDIDATES = {
      image: {
        default: ["kling/v3-omni"],
        buckets: { t2i: ["kling/v3-omni"], i2i: ["kling/v3-omni"] },
      },
      video: {
        default: ["kling/v3-omni"],
        buckets: { i2v: ["kling/v3-omni"], r2v: ["kling/v3-omni"] },
      },
      provider_names: {},
      model_names: {},
    };

    function renderDropdowns(usesReferenceImages: boolean) {
      render(
        <ModelConfigSection
          value={EMPTY_VALUE}
          onChange={() => {}}
          providers={AUDIO_PROVIDERS}
          usesReferenceImages={usesReferenceImages}
          options={{
            videoBackends: ["kling/v3-omni"],
            // 图片下拉刻意放同一个模型：音轨格若被错接到图片侧，这里会显形。
            imageBackends: ["kling/v3-omni"],
            textBackends: [],
            providerNames: { kling: "Kling" },
          }}
          candidates={OMNI_CANDIDATES}
          globalDefaults={EMPTY_GLOBALS}
          enable={{ text: false }}
        />,
      );
    }

    /** 打开指定下拉，读出 v3-omni 那一行的能力线，再关掉——同时只开一个下拉。 */
    async function omniRowIn(user: ReturnType<typeof userEvent.setup>, comboboxName: string) {
      await user.click(screen.getByRole("combobox", { name: comboboxName }));
      const text = screen.getByRole("option", { name: /v3-omni/ }).textContent ?? "";
      await user.keyboard("{Escape}");
      return text;
    }

    it("参考生视频项目：默认层与参考生桶标无声，图生桶不受牵连仍标有声", async () => {
      const user = userEvent.setup();
      renderDropdowns(true);
      // 默认层留空时执行的是 r2v 桶，能力线跟着走
      expect(await omniRowIn(user, "默认视频模型")).toContain("无声");

      await user.click(screen.getAllByText("按用途指定模型")[0]);
      expect(await omniRowIn(user, "参考生视频")).toContain("无声");
      // 项目走参考生路线不代表「图生视频」这一格也该按参考生标注——它就是图生路径本身
      expect(await omniRowIn(user, "图生视频")).toContain("有声");
    });

    it("图生视频项目：默认层与图生桶标有声，参考生桶仍标无声", async () => {
      const user = userEvent.setup();
      renderDropdowns(false);
      expect(await omniRowIn(user, "默认视频模型")).toContain("有声");

      await user.click(screen.getAllByText("按用途指定模型")[0]);
      expect(await omniRowIn(user, "图生视频")).toContain("有声");
      // 项目当前不走参考生路线，但这一格描述的是参考生路径的能力，与项目设置无关
      expect(await omniRowIn(user, "参考生视频")).toContain("无声");
    });

    it("图片侧的默认层与细分项下拉都不带音轨格——音轨是视频概念，图片桶不消费执行路径", async () => {
      const user = userEvent.setup();
      renderDropdowns(true);
      expect(await omniRowIn(user, "默认图片模型")).not.toMatch(/有声|无声/);

      // 图片折叠区排在视频之后
      await user.click(screen.getAllByText("按用途指定模型")[1]);
      expect(await omniRowIn(user, "文生图")).not.toMatch(/有声|无声/);
      expect(await omniRowIn(user, "图生图")).not.toMatch(/有声|无声/);
    });
  });
});
