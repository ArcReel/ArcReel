import { render, screen } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import type { ComponentProps } from "react";
import userEvent from "@testing-library/user-event";
import { ModelConfigSection } from "./ModelConfigSection";
import type { ProviderInfo } from "@/types";

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
        duration_resolution_constraints: {},
        resolutions: [],
        has_audio_track: true,
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
        duration_resolution_constraints: {},
        resolutions: [],
        has_audio_track: true,
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

describe("ModelConfigSection", () => {
  it("renders only the three default-layer selectors when no candidates are supplied", async () => {
    const user = userEvent.setup();
    render(
      <ModelConfigSection
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
    // 不传 candidates（创建向导路径）时只剩三个默认层主下拉：video + image + text
    const comboboxes = screen.getAllByRole("combobox");
    expect(comboboxes).toHaveLength(3);
    expect(screen.queryByText("按用途指定模型")).not.toBeInTheDocument();

    // Opening each dropdown should reveal "使用全局默认" as the default option
    await user.click(comboboxes[0]);
    expect(screen.getByRole("option", { name: /使用全局默认/ })).toBeInTheDocument();
    // Close by clicking again
    await user.click(comboboxes[0]);
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
      expect(container.textContent).not.toMatch(/能力桶|capability bucket/i);
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

    it("auto-expands and counts sub-fields that already carry a value", () => {
      const { container } = renderWithCandidates({
        value: { ...EMPTY_VALUE, videoProviderR2V: "ark/seedance" },
      });
      const videoSection = container.querySelector("details");
      expect(videoSection?.open).toBe(true);
      expect(screen.getByText("已指定 1 项")).toBeInTheDocument();
    });
  });

  it("renders duration buttons based on supported_durations of current video backend", () => {
    const { rerender } = render(
      <ModelConfigSection
        value={{ ...EMPTY_VALUE, videoBackend: "gemini/veo-3" }}
        onChange={() => {}}
        providers={PROVIDERS}
        options={OPTIONS}
        globalDefaults={EMPTY_GLOBALS}
      />,
    );
    expect(screen.getByRole("radio", { name: "4 秒" })).toBeInTheDocument();
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
    expect(screen.getByRole("radio", { name: "5 秒" })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "8 秒" })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "10 秒" })).toBeInTheDocument();
    expect(screen.queryByRole("radio", { name: "4 秒" })).not.toBeInTheDocument();
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

  it("falls back to globalDefaults.video supported_durations when videoBackend is empty (bug repro)", () => {
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
    expect(screen.getByRole("radio", { name: "5 秒" })).toBeInTheDocument();
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
    // 不再 fallback 到 [4,6,8] —— 整个时长卡片不渲染
    expect(screen.queryByRole("radio", { name: "4 秒" })).not.toBeInTheDocument();
    expect(screen.queryByRole("radio", { name: "6 秒" })).not.toBeInTheDocument();
    expect(screen.queryByRole("radio", { name: "8 秒" })).not.toBeInTheDocument();
  });

  it("renders slider when supported_durations is continuous integer range ≥ 5", () => {
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
            duration_resolution_constraints: {},
            resolutions: [],
            has_audio_track: true,
            voice_consistency: "soft",
          },
        },
      },
    ];
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
    expect(screen.getByRole("slider")).toBeInTheDocument();
    expect(screen.queryByRole("radio", { name: "3 秒" })).not.toBeInTheDocument();
  });

  it("hides duration picker when effective backend has no supported_durations", () => {
    render(
      <ModelConfigSection
        value={{ ...EMPTY_VALUE, videoBackend: "unknown/no-such" }}
        onChange={() => {}}
        providers={PROVIDERS}
        options={{ ...OPTIONS, videoBackends: ["unknown/no-such"] }}
        globalDefaults={EMPTY_GLOBALS}
      />,
    );
    expect(screen.queryByRole("slider")).not.toBeInTheDocument();
    expect(screen.queryByRole("radio", { name: /^\d+s$/ })).not.toBeInTheDocument();
  });

  it("marks 'auto' radio as checked when defaultDuration is null", () => {
    render(
      <ModelConfigSection
        value={{ ...EMPTY_VALUE, videoBackend: "gemini/veo-3", defaultDuration: null }}
        onChange={() => {}}
        providers={PROVIDERS}
        options={OPTIONS}
        globalDefaults={EMPTY_GLOBALS}
      />,
    );
    expect(screen.getByRole("radio", { name: "auto" })).toHaveAttribute("aria-checked", "true");
  });

  it("marks the selected duration radio as checked", () => {
    render(
      <ModelConfigSection
        value={{ ...EMPTY_VALUE, videoBackend: "gemini/veo-3", defaultDuration: 6 }}
        onChange={() => {}}
        providers={PROVIDERS}
        options={OPTIONS}
        globalDefaults={EMPTY_GLOBALS}
      />,
    );
    expect(screen.getByRole("radio", { name: "6 秒" })).toHaveAttribute("aria-checked", "true");
    expect(screen.getByRole("radio", { name: "4 秒" })).toHaveAttribute("aria-checked", "false");
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
    await user.click(screen.getByRole("radio", { name: "6 秒" }));
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ defaultDuration: 6 }));
  });

  it("shows an out-of-range notice with no duration radio checked when saved duration is unsupported", () => {
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
    expect(screen.getByText(/10/)).toBeInTheDocument();
    expect(screen.getByText(/不再受当前模型支持/)).toBeInTheDocument();
    // 无任何时长钮处于激活态：auto 与所有数字钮 aria-checked 均为 false
    expect(screen.getByRole("radio", { name: "auto" })).toHaveAttribute("aria-checked", "false");
    for (const sec of ["4 秒", "6 秒", "8 秒"]) {
      expect(screen.getByRole("radio", { name: sec })).toHaveAttribute("aria-checked", "false");
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
    await user.click(screen.getByRole("button", { name: "回退到 auto" }));
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ defaultDuration: null }));
  });

  it("does not show the out-of-range notice when saved duration is supported", () => {
    render(
      <ModelConfigSection
        value={{ ...EMPTY_VALUE, videoBackend: "gemini/veo-3", defaultDuration: 6 }}
        onChange={() => {}}
        providers={PROVIDERS}
        options={OPTIONS}
        globalDefaults={EMPTY_GLOBALS}
      />,
    );
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
            duration_resolution_constraints: {},
            resolutions: [],
            has_audio_track: true,
            voice_consistency: "soft",
          },
        },
      },
    ];
    const user = userEvent.setup();
    const onChange = vi.fn();
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
    const slider = screen.getByRole("slider");
    expect(slider).toBeInTheDocument();
    expect(screen.getByText(/不再受当前模型支持/)).toBeInTheDocument();
    // 越界值的读数/aria-valuetext 忠实显示原值，而非误报为 auto——与未激活的 auto 钮及
    // 点名秒数的越界提示一致
    expect(slider.getAttribute("aria-valuetext")).toMatch(/20/);
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
          duration_resolution_constraints: { "1080p": [8], "4k": [8] },
          reference_image_durations: [8],
          resolutions: ["720p", "1080p", "4k"],
          has_audio_track: true,
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

  it("offers every supported duration at an unconstrained resolution", () => {
    renderVeo({ videoResolution: "720p" });
    for (const sec of ["4 秒", "6 秒", "8 秒"]) {
      expect(screen.getByRole("radio", { name: sec })).toBeInTheDocument();
    }
  });

  it.each(["1080p", "4k"])("offers only 8s at %s", (resolution) => {
    renderVeo({ videoResolution: resolution });
    expect(screen.getByRole("radio", { name: "8 秒" })).toBeInTheDocument();
    expect(screen.queryByRole("radio", { name: "4 秒" })).not.toBeInTheDocument();
    expect(screen.queryByRole("radio", { name: "6 秒" })).not.toBeInTheDocument();
  });

  it("offers only 8s on the reference-video path even at 720p", () => {
    renderVeo({ videoResolution: "720p", usesReferenceImages: true });
    expect(screen.getByRole("radio", { name: "8 秒" })).toBeInTheDocument();
    expect(screen.queryByRole("radio", { name: "4 秒" })).not.toBeInTheDocument();
  });

  // 警告文案按越界成因分开：模型本身仍支持 4 秒，指向「模型不支持」会把用户引去换模型。
  it.each([
    ["1080p 分辨率", { videoResolution: "1080p" }, /当前分辨率下不可用/],
    ["参考生视频模式", { videoResolution: "720p", usesReferenceImages: true }, /参考生视频模式下不可用/],
  ])("warns about a saved 4s duration under %s", (_label, overrides, expected) => {
    renderVeo({ ...overrides, defaultDuration: 4 });
    expect(screen.getByRole("alert")).toHaveTextContent(expected);
    expect(screen.getByRole("alert")).not.toHaveTextContent(/不再受当前模型支持/);
    expect(screen.getByRole("radio", { name: "8 秒" })).toHaveAttribute("aria-checked", "false");
  });

  it("keeps a saved 4s duration valid when neither constraint applies", () => {
    renderVeo({ videoResolution: "720p", defaultDuration: 4 });
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "4 秒" })).toHaveAttribute("aria-checked", "true");
  });
});
