import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
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
    default_text_backend: "",
    text_backend_simple: "",
    text_backend_complex: "",
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
    default_text_backend: "gemini/g25",
    text_backend_simple: "gemini/g25",
    text_backend_complex: "gemini/g25",
  },
};

const FAKE_CANDIDATES = {
  image: {
    default: ["gemini/nano-banana"],
    buckets: { t2i: ["gemini/nano-banana"], i2i: ["gemini/nano-banana"] },
  },
  video: {
    default: ["gemini/veo-3"],
    buckets: { i2v: ["gemini/veo-3"], r2v: [] },
  },
  provider_names: {},
};

function mockBuiltinAgentProfile() {
  vi.spyOn(API, "getAgentProfileStatus").mockResolvedValue({
    customized: false,
    customized_files: [],
  });
}

function renderAt(path: string) {
  const location = memoryLocation({ path, record: true });
  return {
    ...render(
      <Router hook={location.hook}>
        <Route path="/app/projects/:projectName/settings" component={ProjectSettingsPage} />
      </Router>,
    ),
    location,
  };
}

describe("ProjectSettingsPage – style picker", () => {
  beforeEach(() => {
    useAppStore.setState(useAppStore.getInitialState(), true);
    vi.restoreAllMocks();
    vi.spyOn(API, "getSystemConfig").mockResolvedValue(FAKE_CONFIG as unknown as Awaited<ReturnType<typeof API.getSystemConfig>>);
    vi.spyOn(API, "getModelCandidates").mockResolvedValue(
      FAKE_CANDIDATES as unknown as Awaited<ReturnType<typeof API.getModelCandidates>>,
    );
    vi.spyOn(providerModels, "getProviderModels").mockResolvedValue([]);
    vi.spyOn(providerModels, "getCustomProviderModels").mockResolvedValue([]);
    mockBuiltinAgentProfile();
  });

  it("shows customized Agent Profile files and resets only after destructive confirmation", async () => {
    vi.spyOn(API, "getProject").mockResolvedValue({
      project: { title: "Demo", episodes: [], characters: {}, clues: {} },
      scripts: {},
    } as unknown as Awaited<ReturnType<typeof API.getProject>>);
    vi.spyOn(API, "getAgentProfileStatus").mockResolvedValue({
      customized: true,
      customized_files: ["CLAUDE.md", ".claude/agents/legacy.md"],
    });
    const resetSpy = vi.spyOn(API, "resetAgentProfile").mockResolvedValue({
      customized: false,
      customized_files: [],
    });

    renderAt("/app/projects/demo/settings");

    expect(await screen.findByText("CLAUDE.md")).toBeInTheDocument();
    expect(screen.getByText(".claude/agents/legacy.md")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /重置为内置配置|Reset to built-in/ }));
    expect(resetSpy).not.toHaveBeenCalled();
    expect(await screen.findByText(/将丢弃|discard/)).toBeInTheDocument();
    expect(screen.getByText(/重新连接|reconnect/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /确认重置|Confirm reset/ }));
    await waitFor(() => expect(resetSpy).toHaveBeenCalledWith("demo"));
    expect(await screen.findByText(/使用内置配置|Using built-in/)).toBeInTheDocument();
  });

  it("does not carry Agent Profile state or reset confirmation across projects", async () => {
    vi.spyOn(API, "getProject").mockResolvedValue({
      project: { title: "Demo", episodes: [], characters: {}, clues: {} },
      scripts: {},
    } as unknown as Awaited<ReturnType<typeof API.getProject>>);
    vi.spyOn(API, "getAgentProfileStatus").mockImplementation((name) => (
      name === "project-a"
        ? Promise.resolve({ customized: true, customized_files: ["CLAUDE.md"] })
        : new Promise(() => {})
    ));
    const resetSpy = vi.spyOn(API, "resetAgentProfile");
    const { location } = renderAt("/app/projects/project-a/settings");

    expect(await screen.findByText("CLAUDE.md")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /重置为内置配置|Reset to built-in/ }));
    expect(await screen.findByText(/将丢弃|discard/)).toBeInTheDocument();

    act(() => location.navigate("/app/projects/project-b/settings"));

    await waitFor(() => expect(screen.queryByText("CLAUDE.md")).not.toBeInTheDocument());
    expect(screen.queryByText(/将丢弃|discard/)).not.toBeInTheDocument();
    expect(resetSpy).not.toHaveBeenCalled();
  });

  it("requires another confirmation when customized files change before reset", async () => {
    const initialStatus = { customized: true, customized_files: ["CLAUDE.md"] };
    const changedStatus = {
      customized: true,
      customized_files: [".claude/agents/new.md", "CLAUDE.md"],
    };
    vi.spyOn(API, "getProject").mockResolvedValue({
      project: { title: "Demo", episodes: [], characters: {}, clues: {} },
      scripts: {},
    } as unknown as Awaited<ReturnType<typeof API.getProject>>);
    vi.spyOn(API, "getAgentProfileStatus")
      .mockResolvedValueOnce(initialStatus)
      .mockResolvedValueOnce(initialStatus)
      .mockResolvedValue(changedStatus);
    const resetSpy = vi.spyOn(API, "resetAgentProfile");
    renderAt("/app/projects/demo/settings");

    expect(await screen.findByText("CLAUDE.md")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /重置为内置配置|Reset to built-in/ }));
    expect(await screen.findByText(/将丢弃|discard/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /确认重置|Confirm reset/ }));

    expect(await screen.findAllByText(".claude/agents/new.md")).toHaveLength(2);
    expect(resetSpy).not.toHaveBeenCalled();
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

    // 项目未覆盖 → 默认主下拉显示全局默认作为生效值
    const imageTrigger = await screen.findByRole("combobox", {
      name: /^(默认图片模型|Default image model)$/,
    });
    expect(imageTrigger).toHaveTextContent(/跟随全局默认|Use global default/);
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

  it("shows the generation route read-only, with no control to change it", async () => {
    vi.spyOn(API, "getProject").mockResolvedValue({
      project: {
        title: "Demo",
        generation_mode: "reference_video",
        episodes: [],
        characters: {},
        clues: {},
      },
      scripts: {},
    } as unknown as Awaited<ReturnType<typeof API.getProject>>);

    renderAt("/app/projects/demo/settings");

    expect(await screen.findByText(/跳过分镜图，直接用角色、场景、道具图作为参考生成视频/)).toBeInTheDocument();
    expect(screen.getByText(/生成方式创建后不可更改/)).toBeInTheDocument();
    expect(screen.queryByRole("radio", { name: /参考生视频|分镜图生视频/ })).not.toBeInTheDocument();
    // 参考生视频下不呈现宫格开关
    expect(screen.queryByRole("switch", { name: /多宫格分镜/ })).not.toBeInTheDocument();
  });

  it("saves the grid assembly toggle on the storyboard route", async () => {
    vi.spyOn(API, "getProject").mockResolvedValue({
      project: {
        title: "Demo",
        generation_mode: "storyboard",
        grid_storyboard: false,
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

    const toggle = await screen.findByRole("switch", { name: /多宫格分镜/ });
    expect(toggle).toHaveAttribute("aria-checked", "false");
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-checked", "true");

    fireEvent.click(screen.getByRole("button", { name: /^(保存|Save)$/i }));
    await waitFor(() => {
      expect(updateSpy).toHaveBeenCalledWith("demo", expect.objectContaining({ grid_storyboard: true }));
    });
    // 生成模式不在 PATCH 面上
    expect(updateSpy.mock.calls[0][1]).not.toHaveProperty("generation_mode");
  });

  it("hides the grid toggle for ad projects", async () => {
    vi.spyOn(API, "getProject").mockResolvedValue({
      project: {
        title: "Demo",
        content_mode: "ad",
        generation_mode: "storyboard",
        episodes: [],
        characters: {},
        clues: {},
      },
      scripts: {},
    } as unknown as Awaited<ReturnType<typeof API.getProject>>);

    renderAt("/app/projects/demo/settings");

    expect(await screen.findByText(/先为每个分镜生成分镜图/)).toBeInTheDocument();
    expect(screen.queryByRole("switch", { name: /多宫格分镜/ })).not.toBeInTheDocument();
  });
});

describe("ProjectSettingsPage – model_settings resolution", () => {
  beforeEach(() => {
    useAppStore.setState(useAppStore.getInitialState(), true);
    vi.restoreAllMocks();
    vi.spyOn(API, "getModelCandidates").mockResolvedValue(
      FAKE_CANDIDATES as unknown as Awaited<ReturnType<typeof API.getModelCandidates>>,
    );
    vi.spyOn(providerModels, "getProviderModels").mockResolvedValue([]);
    vi.spyOn(providerModels, "getCustomProviderModels").mockResolvedValue([]);
    mockBuiltinAgentProfile();
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
            audio_track: "controllable",
            reference_route_audio_track: "controllable",
            voice_consistency: "soft",
          },
          "nano-banana": {
            display_name: "Nano Banana",
            media_type: "image",
            capabilities: [],
            default: true,
            supported_durations: [],
            duration_resolution_constraints: {},
            resolutions: ["720p", "1080p"],
            audio_track: "always_off",
            reference_route_audio_track: "always_off",
            voice_consistency: "none",
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

  it("reads and writes the image resolution under the executing text-to-image model", async () => {
    // 项目默认层与文生图槽指向不同模型：后端按执行模型查 model_settings，故读写都挂在
    // 文生图槽那个模型上——挂错 key 时用户选的分辨率会被静默忽略，且重载读回旧值。
    vi.spyOn(API, "getSystemConfig").mockResolvedValue({
      ...FAKE_CONFIG_WITH_DEFAULTS,
    } as unknown as Awaited<ReturnType<typeof API.getSystemConfig>>);
    vi.spyOn(API, "getProject").mockResolvedValue({
      project: {
        title: "Demo",
        video_backend: "gemini/veo-3",
        default_image_backend: "gemini/nano-banana",
        image_provider_t2i: "openai/gpt-image",
        model_settings: {
          "gemini/nano-banana": { resolution: "1080p" },
          "openai/gpt-image": { resolution: "720p" },
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
    await screen.findByRole("radio", { name: /竖屏 9:16/ });
    fireEvent.click(screen.getByRole("button", { name: /^(保存|Save)$/i }));

    await waitFor(() => {
      expect(updateSpy).toHaveBeenCalledWith(
        "demo",
        expect.objectContaining({
          model_settings: expect.objectContaining({
            // 读到的是文生图执行模型的 720p，写回的也是同一个 key
            "openai/gpt-image": expect.objectContaining({ resolution: "720p" }),
          }),
        }),
      );
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

describe("ProjectSettingsPage – 按用途指定模型", () => {
  beforeEach(() => {
    useAppStore.setState(useAppStore.getInitialState(), true);
    vi.restoreAllMocks();
    vi.spyOn(API, "getSystemConfig").mockResolvedValue(
      FAKE_CONFIG_WITH_DEFAULTS as unknown as Awaited<ReturnType<typeof API.getSystemConfig>>,
    );
    vi.spyOn(API, "getModelCandidates").mockResolvedValue(
      FAKE_CANDIDATES as unknown as Awaited<ReturnType<typeof API.getModelCandidates>>,
    );
    vi.spyOn(providerModels, "getProviderModels").mockResolvedValue([]);
    vi.spyOn(providerModels, "getCustomProviderModels").mockResolvedValue([]);
    mockBuiltinAgentProfile();
  });

  it("loads project sub-field overrides and writes each back to its own key", async () => {
    vi.spyOn(API, "getProject").mockResolvedValue({
      project: {
        title: "Demo",
        video_provider_i2v: "gemini/veo-3",
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

    // 已配置的细分项让所在通道初始展开，值可见
    const i2v = await screen.findByRole("combobox", { name: /^(图生视频|Image to video)$/ });
    expect(i2v).toHaveTextContent(/veo-3/);

    fireEvent.click(screen.getByRole("radio", { name: /横屏 16:9|16:9/ }));
    fireEvent.click(screen.getByRole("button", { name: /^(保存|Save)$/i }));

    await waitFor(() => {
      expect(updateSpy).toHaveBeenCalledWith(
        "demo",
        expect.objectContaining({
          video_provider_i2v: "gemini/veo-3",
          video_provider_r2v: null,
          default_image_backend: null,
          image_provider_t2i: null,
          image_provider_i2i: null,
        }),
      );
    });
  });

  it("shows the reading unit of the project source language and saves the speech rate", async () => {
    vi.spyOn(API, "getProject").mockResolvedValue({
      project: {
        title: "Demo",
        source_language: "en",
        speech_rate_units_per_second: 3,
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

    const input = await screen.findByLabelText(/^(语速（可选）|Pace \(optional\))$/);
    expect(input).toHaveValue(3);
    // en 项目按词计
    expect(screen.getByText("词/秒")).toBeInTheDocument();

    fireEvent.change(input, { target: { value: "4.5" } });
    fireEvent.click(screen.getByRole("button", { name: /^(保存|Save)$/i }));

    await waitFor(() => {
      expect(updateSpy).toHaveBeenCalledWith(
        "demo",
        expect.objectContaining({ speech_rate_units_per_second: 4.5 }),
      );
    });
  });

  it("blocks saving while the speech rate is out of range", async () => {
    vi.spyOn(API, "getProject").mockResolvedValue({
      project: { title: "Demo", episodes: [], characters: {}, clues: {} },
      scripts: {},
    } as unknown as Awaited<ReturnType<typeof API.getProject>>);

    renderAt("/app/projects/demo/settings");

    const input = await screen.findByLabelText(/^(语速（可选）|Pace \(optional\))$/);
    fireEvent.change(input, { target: { value: "25" } });

    expect(screen.getByRole("button", { name: /^(保存|Save)$/i })).toBeDisabled();
  });
});
