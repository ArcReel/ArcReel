import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Router, Route } from "wouter";
import { memoryLocation } from "wouter/memory-location";
import "@/i18n";
import { API } from "@/api";
import * as providerModels from "@/utils/provider-models";
import { useAppStore } from "@/stores/app-store";
import { ProjectSettingsPage } from "@/components/pages/ProjectSettingsPage";

const FAKE_CONFIG = {
  options: { video_backends: [], image_backends: [], text_backends: [], provider_names: {} },
  settings: {
    default_video_backend: "",
    default_image_backend: "",
    text_backend_script: "",
    text_backend_overview: "",
    text_backend_style: "",
  },
};

const FAKE_CONFIG_WITH_DEFAULTS = {
  options: {
    video_backends: ["gemini/veo-3"],
    image_backends: ["gemini/nano-banana"],
    text_backends: ["gemini/g25"],
    provider_names: { gemini: "Gemini" },
  },
  settings: {
    default_video_backend: "gemini/veo-3",
    default_image_backend: "gemini/nano-banana",
    default_image_backend_t2i: "gemini/nano-banana",
    default_image_backend_i2i: "gemini/nano-banana",
    text_backend_script: "gemini/g25",
    text_backend_overview: "gemini/g25",
    text_backend_style: "gemini/g25",
  },
};

function renderAt(path: string) {
  const location = memoryLocation({ path, record: true });
  return render(
    <Router hook={location.hook}>
      <Route path="/app/projects/:projectName/settings" component={ProjectSettingsPage} />
    </Router>,
  );
}

describe("ProjectSettingsPage – style picker", () => {
  beforeEach(() => {
    useAppStore.setState(useAppStore.getInitialState(), true);
    vi.restoreAllMocks();
    vi.spyOn(API, "getSystemConfig").mockResolvedValue(FAKE_CONFIG as unknown as Awaited<ReturnType<typeof API.getSystemConfig>>);
    vi.spyOn(providerModels, "getProviderModels").mockResolvedValue([]);
    vi.spyOn(providerModels, "getCustomProviderModels").mockResolvedValue([]);
  });

  it("loads a project with style_template_id and selects the matching template card by default", async () => {
    vi.spyOn(API, "getProject").mockResolvedValue({
      project: {
        title: "Demo",
        style_template_id: "live_zhang_yimou",
        style: "画风：参考张艺谋电影风格",
        episodes: [],
        characters: {},
        clues: {},
      },
      scripts: {},
    } as unknown as Awaited<ReturnType<typeof API.getProject>>);

    renderAt("/app/projects/demo/settings");

    await waitFor(() => {
      // Selected card has aria-pressed=true
      const selected = screen.getByRole("button", { name: /张艺谋/, pressed: true });
      expect(selected).toBeInTheDocument();
    });
  });

  it("loads a project with style_image and switches to custom tab with existing preview", async () => {
    vi.spyOn(API, "getProject").mockResolvedValue({
      project: {
        title: "Demo",
        style_image: "style_reference.png",
        style_description: "old desc",
        episodes: [],
        characters: {},
        clues: {},
      },
      scripts: {},
    } as unknown as Awaited<ReturnType<typeof API.getProject>>);

    renderAt("/app/projects/demo/settings");

    await waitFor(() => {
      const img = screen.getByAltText(/上传风格参考图|Upload style reference/) as HTMLImageElement;
      expect(img.src).toContain("/api/v1/files/demo/style_reference.png");
    });
  });

  it("clearing the reference image keeps save enabled and triggers clear PATCH", async () => {
    vi.spyOn(API, "getProject").mockResolvedValue({
      project: {
        title: "Demo",
        style_image: "style_reference.png",
        episodes: [],
        characters: {},
        clues: {},
      },
      scripts: {},
    } as unknown as Awaited<ReturnType<typeof API.getProject>>);
    const updateSpy = vi.spyOn(API, "updateProject").mockResolvedValue({
      success: true,
      project: { title: "Demo" } as unknown as Awaited<ReturnType<typeof API.updateProject>>["project"],
    });

    renderAt("/app/projects/demo/settings");

    await waitFor(() => screen.getByAltText(/上传风格参考图|Upload style reference/));
    const removeBtn = screen.getByRole("button", { name: /^remove$/i });
    fireEvent.click(removeBtn);

    // 移除自定义图后 save 应可点：保存即清除后端残留 style_image / description
    const saveBtn = screen.getByRole("button", { name: /保存风格|Save style/ });
    expect(saveBtn).not.toBeDisabled();
    fireEvent.click(saveBtn);

    await waitFor(() => {
      expect(updateSpy).toHaveBeenCalledWith("demo", {
        style_template_id: null,
        clear_style_image: true,
      });
    });
  });

  it("clicking 取消风格 when project has a template sends clear PATCH", async () => {
    vi.spyOn(API, "getProject").mockResolvedValue({
      project: {
        title: "Demo",
        style_template_id: "live_premium_drama",
        style: "画风：...",
        episodes: [],
        characters: {},
        clues: {},
      },
      scripts: {},
    } as unknown as Awaited<ReturnType<typeof API.getProject>>);
    const updateSpy = vi.spyOn(API, "updateProject").mockResolvedValue({
      success: true,
      project: { title: "Demo" } as unknown as Awaited<ReturnType<typeof API.updateProject>>["project"],
    });

    renderAt("/app/projects/demo/settings");

    // 等到 style picker 已经 mount（能找到保存按钮）
    await screen.findByRole("button", { name: /保存风格|Save style/ });

    const clearBtn = screen.getByRole("button", { name: /取消风格|Remove style/ });
    fireEvent.click(clearBtn);

    const saveBtn = screen.getByRole("button", { name: /保存风格|Save style/ });
    expect(saveBtn).not.toBeDisabled();
    fireEvent.click(saveBtn);

    await waitFor(() => {
      expect(updateSpy).toHaveBeenCalledWith("demo", {
        style_template_id: null,
        clear_style_image: true,
      });
    });
  });

  it("falls back to 9:16 aspect ratio highlight when project has no aspect_ratio set", async () => {
    vi.spyOn(API, "getProject").mockResolvedValue({
      project: {
        title: "Demo",
        episodes: [],
        characters: {},
        clues: {},
      },
      scripts: {},
    } as unknown as Awaited<ReturnType<typeof API.getProject>>);

    renderAt("/app/projects/demo/settings");

    const portrait = await screen.findByRole("radio", { name: /竖屏 9:16/ });
    expect(portrait).toBeChecked();
    const landscape = screen.getByRole("radio", { name: /横屏 16:9/ });
    expect(landscape).not.toBeChecked();
  });

  it("shows 'follow global default · provider · model' in model triggers when project has no model override", async () => {
    vi.spyOn(API, "getSystemConfig").mockResolvedValue(
      FAKE_CONFIG_WITH_DEFAULTS as unknown as Awaited<ReturnType<typeof API.getSystemConfig>>,
    );
    vi.spyOn(API, "getProject").mockResolvedValue({
      project: {
        title: "Demo",
        episodes: [],
        characters: {},
        clues: {},
      },
      scripts: {},
    } as unknown as Awaited<ReturnType<typeof API.getProject>>);

    renderAt("/app/projects/demo/settings");

    // Wait for config to load and a model trigger to render (T2I trigger)
    const imageTrigger = await screen.findByRole("combobox", { name: /文生图|Text-to-Image|T2I/i });
    expect(imageTrigger).toHaveTextContent(/跟随全局默认/);
    expect(imageTrigger).toHaveTextContent(/nano-banana/);
  });

  it("saves a template change via PATCH style_template_id", async () => {
    vi.spyOn(API, "getProject").mockResolvedValue({
      project: {
        title: "Demo",
        style_template_id: "live_premium_drama",
        style: "...",
        episodes: [],
        characters: {},
        clues: {},
      },
      scripts: {},
    } as unknown as Awaited<ReturnType<typeof API.getProject>>);
    const updateSpy = vi.spyOn(API, "updateProject").mockResolvedValue({
      success: true,
      project: { title: "Demo", style_template_id: "live_zhang_yimou" } as unknown as Awaited<ReturnType<typeof API.updateProject>>["project"],
    });

    renderAt("/app/projects/demo/settings");

    const card = await screen.findByRole("button", { name: /张艺谋/ });
    fireEvent.click(card);

    const saveBtn = screen.getByRole("button", { name: /保存风格|Save style/ });
    expect(saveBtn).not.toBeDisabled();
    fireEvent.click(saveBtn);

    await waitFor(() => {
      expect(updateSpy).toHaveBeenCalledWith("demo", { style_template_id: "live_zhang_yimou" });
    });
  });

  it("switches generation_mode to reference_video and marks the save button enabled", async () => {
    vi.spyOn(API, "getProject").mockResolvedValue({
      project: {
        title: "Demo",
        generation_mode: "storyboard",
        episodes: [],
        characters: {},
        clues: {},
      },
      scripts: {},
    } as unknown as Awaited<ReturnType<typeof API.getProject>>);
    vi.spyOn(API, "updateProject").mockResolvedValue({
      success: true,
      project: { title: "Demo" } as unknown as Awaited<ReturnType<typeof API.updateProject>>["project"],
    });

    renderAt("/app/projects/demo/settings");

    // Wait for the generation mode selector to appear (3 radios total)
    const referenceVideoRadio = await screen.findByRole("radio", { name: /参考生视频|Reference-to-Video/i });
    expect(referenceVideoRadio).not.toBeChecked();

    fireEvent.click(referenceVideoRadio);

    // After switching to reference_video the radio should be checked (dirty state)
    expect(referenceVideoRadio).toBeChecked();

    // The main save button should be enabled (it is never disabled except while saving)
    const saveBtn = screen.getByRole("button", { name: /^(保存|Save)$/i });
    expect(saveBtn).not.toBeDisabled();
  });
});

describe("ProjectSettingsPage – model_settings resolution", () => {
  beforeEach(() => {
    useAppStore.setState(useAppStore.getInitialState(), true);
    vi.restoreAllMocks();
    vi.spyOn(providerModels, "getProviderModels").mockResolvedValue([]);
    vi.spyOn(providerModels, "getCustomProviderModels").mockResolvedValue([]);
  });

  it("loads existing model_settings resolution into video/image pickers", async () => {
    vi.spyOn(API, "getSystemConfig").mockResolvedValue({
      ...FAKE_CONFIG_WITH_DEFAULTS,
    } as unknown as Awaited<ReturnType<typeof API.getSystemConfig>>);
    // 提供含 resolutions 的 provider，使 ResolutionPicker 能够渲染
    vi.spyOn(providerModels, "getProviderModels").mockResolvedValue([
      {
        id: "gemini",
        display_name: "Gemini",
        description: "",
        status: "ready",
        media_types: ["video", "image"],
        capabilities: [],
        configured_keys: [],
        missing_keys: [],
        models: {
          "veo-3": {
            display_name: "Veo 3",
            media_type: "video",
            capabilities: [],
            default: true,
            supported_durations: [5, 8],
            duration_resolution_constraints: {},
            resolutions: ["720p", "1080p"],
          },
          "nano-banana": {
            display_name: "Nano Banana",
            media_type: "image",
            capabilities: [],
            default: true,
            supported_durations: [],
            duration_resolution_constraints: {},
            resolutions: ["720p", "1080p"],
          },
        },
      },
    ] as Awaited<ReturnType<typeof providerModels.getProviderModels>>);
    vi.spyOn(API, "getProject").mockResolvedValue({
      project: {
        title: "Demo",
        video_backend: "gemini/veo-3",
        image_provider_t2i: "gemini/nano-banana",
        image_provider_i2i: "gemini/nano-banana",
        model_settings: {
          "gemini/veo-3": { resolution: "1080p" },
          "gemini/nano-banana": { resolution: "720p" },
        },
        episodes: [],
        characters: {},
        clues: {},
      },
      scripts: {},
    } as unknown as Awaited<ReturnType<typeof API.getProject>>);

    renderAt("/app/projects/demo/settings");

    // 等待 ResolutionPicker 出现并验证已加载的初始值
    // select 模式的 ResolutionPicker 渲染为 <select>，当前值会是对应 option selected
    await waitFor(() => {
      const selects = screen.getAllByRole("combobox");
      // 找到视频分辨率 select（aria-label 为 "分辨率"）
      const resSelects = selects.filter((el) =>
        el.getAttribute("aria-label")?.includes("分辨率") || el.getAttribute("aria-label")?.includes("Resolution"),
      );
      expect(resSelects.length).toBeGreaterThan(0);
      // 验证已加载的值
      const values = resSelects.map((el) => (el as HTMLSelectElement).value);
      expect(values).toContain("1080p");
      expect(values).toContain("720p");
    });
  });

  it("saves resolution changes via updateProject with model_settings", async () => {
    vi.spyOn(API, "getSystemConfig").mockResolvedValue({
      ...FAKE_CONFIG_WITH_DEFAULTS,
    } as unknown as Awaited<ReturnType<typeof API.getSystemConfig>>);
    // getProject 会被 handleSave 内调用一次（获取 existingModelSettings），mock 始终返回相同 project
    vi.spyOn(API, "getProject").mockResolvedValue({
      project: {
        title: "Demo",
        video_backend: "gemini/veo-3",
        image_provider_t2i: "gemini/nano-banana",
        image_provider_i2i: "gemini/nano-banana",
        model_settings: {
          "gemini/veo-3": { resolution: "1080p" },
          "gemini/nano-banana": { resolution: "720p" },
        },
        episodes: [],
        characters: {},
        clues: {},
      },
      scripts: {},
    } as unknown as Awaited<ReturnType<typeof API.getProject>>);
    const updateSpy = vi.spyOn(API, "updateProject").mockResolvedValue({
      success: true,
      project: { title: "Demo" } as unknown as Awaited<ReturnType<typeof API.updateProject>>["project"],
    });

    renderAt("/app/projects/demo/settings");

    // 等配置加载完
    await screen.findByRole("radio", { name: /竖屏 9:16/ });

    const saveBtn = screen.getByRole("button", { name: /^(保存|Save)$/i });
    fireEvent.click(saveBtn);

    await waitFor(() => {
      expect(updateSpy).toHaveBeenCalledWith(
        "demo",
        expect.objectContaining({
          model_settings: expect.objectContaining({
            "gemini/veo-3": expect.objectContaining({ resolution: "1080p" }),
            "gemini/nano-banana": expect.objectContaining({ resolution: "720p" }),
          }),
        }),
      );
    });
  });
});
