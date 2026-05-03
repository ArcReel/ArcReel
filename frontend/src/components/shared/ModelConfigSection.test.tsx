import { render, screen } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
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
  imageBackendT2I: "",
  imageBackendI2I: "",
  textBackendScript: "",
  textBackendOverview: "",
  textBackendStyle: "",
  defaultDuration: null,
  videoResolution: null,
  imageResolution: null,
} as const;

describe("ModelConfigSection", () => {
  it("renders 5 model selectors and shows '使用全局默认' inside each dropdown when all backends are empty", async () => {
    const user = userEvent.setup();
    render(
      <ModelConfigSection
        value={EMPTY_VALUE}
        onChange={() => {}}
        providers={PROVIDERS}
        options={OPTIONS}
        globalDefaults={{
          video: "gemini/veo-3",
          imageT2I: "gemini/nano-banana",
          imageI2I: "gemini/nano-banana",
          textScript: "gemini/g25",
          textOverview: "gemini/g25",
          textStyle: "gemini/g25",
        }}
      />,
    );
    // 5 combobox triggers — 单下拉模式下 image 只渲染 1 个（spec: 默认渲染单下拉，
    // 仅当所选模型 caps 单一时才露出第二个槽位）：1 video + 1 image + 3 text
    const comboboxes = screen.getAllByRole("combobox");
    expect(comboboxes).toHaveLength(5);

    // Opening each dropdown should reveal "使用全局默认" as the default option
    await user.click(comboboxes[0]);
    expect(screen.getByRole("option", { name: /使用全局默认/ })).toBeInTheDocument();
    // Close by clicking again
    await user.click(comboboxes[0]);
  });

  it("renders duration buttons based on supported_durations of current video backend", () => {
    const { rerender } = render(
      <ModelConfigSection
        value={{ ...EMPTY_VALUE, videoBackend: "gemini/veo-3" }}
        onChange={() => {}}
        providers={PROVIDERS}
        options={OPTIONS}
        globalDefaults={{ video: "", imageT2I: "", imageI2I: "", textScript: "", textOverview: "", textStyle: "" }}
      />,
    );
    expect(screen.getByRole("radio", { name: "4s" })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "6s" })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "8s" })).toBeInTheDocument();
    expect(screen.queryByRole("radio", { name: "5s" })).not.toBeInTheDocument();

    rerender(
      <ModelConfigSection
        value={{ ...EMPTY_VALUE, videoBackend: "ark/seedance" }}
        onChange={() => {}}
        providers={PROVIDERS}
        options={OPTIONS}
        globalDefaults={{ video: "", imageT2I: "", imageI2I: "", textScript: "", textOverview: "", textStyle: "" }}
      />,
    );
    expect(screen.getByRole("radio", { name: "5s" })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "8s" })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "10s" })).toBeInTheDocument();
    expect(screen.queryByRole("radio", { name: "4s" })).not.toBeInTheDocument();
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
        globalDefaults={{ video: "", imageT2I: "", imageI2I: "", textScript: "", textOverview: "", textStyle: "" }}
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
        globalDefaults={{ video: "", imageT2I: "", imageI2I: "", textScript: "", textOverview: "", textStyle: "" }}
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

  it("respects enable.video=false to hide the video card", () => {
    render(
      <ModelConfigSection
        value={EMPTY_VALUE}
        onChange={() => {}}
        providers={PROVIDERS}
        options={OPTIONS}
        globalDefaults={{ video: "", imageT2I: "", imageI2I: "", textScript: "", textOverview: "", textStyle: "" }}
        enable={{ video: false }}
      />,
    );
    // No combobox for video model should be visible
    expect(screen.queryByRole("combobox", { name: /视频模型/ })).not.toBeInTheDocument();
    // 单下拉模式下 image card 主下拉 label 是「图片模型」（不是「文生图」/「图生图」）
    expect(screen.getByRole("combobox", { name: /^图片模型$/ })).toBeInTheDocument();
  });

  it("falls back to globalDefaults.video supported_durations when videoBackend is empty (bug repro)", () => {
    render(
      <ModelConfigSection
        value={EMPTY_VALUE}
        onChange={() => {}}
        providers={PROVIDERS}
        options={OPTIONS}
        globalDefaults={{
          video: "ark/seedance",
          imageT2I: "",
          imageI2I: "",
          textScript: "",
          textOverview: "",
          textStyle: "",
        }}
      />,
    );
    // Should reflect ark/seedance's supported_durations [5, 8, 10]
    expect(screen.getByRole("radio", { name: "5s" })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "8s" })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "10s" })).toBeInTheDocument();
    // Should NOT show DEFAULT_DURATIONS buttons that ark/seedance doesn't support
    expect(screen.queryByRole("radio", { name: "4s" })).not.toBeInTheDocument();
    expect(screen.queryByRole("radio", { name: "6s" })).not.toBeInTheDocument();
  });

  it("hides duration picker when videoBackend is empty and no global default", () => {
    render(
      <ModelConfigSection
        value={EMPTY_VALUE}
        onChange={() => {}}
        providers={PROVIDERS}
        options={OPTIONS}
        globalDefaults={{ video: "", imageT2I: "", imageI2I: "", textScript: "", textOverview: "", textStyle: "" }}
      />,
    );
    // 不再 fallback 到 [4,6,8] —— 整个时长卡片不渲染
    expect(screen.queryByRole("radio", { name: "4s" })).not.toBeInTheDocument();
    expect(screen.queryByRole("radio", { name: "6s" })).not.toBeInTheDocument();
    expect(screen.queryByRole("radio", { name: "8s" })).not.toBeInTheDocument();
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
        globalDefaults={{ video: "", imageT2I: "", imageI2I: "", textScript: "", textOverview: "", textStyle: "" }}
      />,
    );
    // 连续区间 → slider，不再有按钮组（除 auto + slider 自身的 radio）
    expect(screen.getByRole("slider")).toBeInTheDocument();
    expect(screen.queryByRole("radio", { name: "3s" })).not.toBeInTheDocument();
  });

  it("hides duration picker when effective backend has no supported_durations", () => {
    render(
      <ModelConfigSection
        value={{ ...EMPTY_VALUE, videoBackend: "unknown/no-such" }}
        onChange={() => {}}
        providers={PROVIDERS}
        options={{ ...OPTIONS, videoBackends: ["unknown/no-such"] }}
        globalDefaults={{ video: "", imageT2I: "", imageI2I: "", textScript: "", textOverview: "", textStyle: "" }}
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
        globalDefaults={{ video: "", imageT2I: "", imageI2I: "", textScript: "", textOverview: "", textStyle: "" }}
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
        globalDefaults={{ video: "", imageT2I: "", imageI2I: "", textScript: "", textOverview: "", textStyle: "" }}
      />,
    );
    expect(screen.getByRole("radio", { name: "6s" })).toHaveAttribute("aria-checked", "true");
    expect(screen.getByRole("radio", { name: "4s" })).toHaveAttribute("aria-checked", "false");
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
        globalDefaults={{ video: "", imageT2I: "", imageI2I: "", textScript: "", textOverview: "", textStyle: "" }}
      />,
    );
    await user.click(screen.getByRole("radio", { name: "6s" }));
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ defaultDuration: 6 }));
  });
});
