import { render, screen, fireEvent, waitFor, act, within } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

// Stub URL object APIs not available in jsdom
globalThis.URL.createObjectURL ??= vi.fn(() => "blob:mock");
globalThis.URL.revokeObjectURL ??= vi.fn();
import i18n from "@/i18n";
import { CreateProjectModal } from "./CreateProjectModal";
import { API } from "@/api";
import { useProjectsStore } from "@/stores/projects-store";
import { useAppStore } from "@/stores/app-store";

// Mock wouter navigation
const navigateMock = vi.fn();
vi.mock("wouter", () => ({
  useLocation: () => ["/app/projects", navigateMock],
}));

const mockSysConfig = {
  settings: {
    default_video_backend: "",
    default_image_backend: "",
    default_text_backend: "",
    text_backend_simple: "",
    text_backend_complex: "",
    video_generate_audio: false,
    anthropic_api_key: { is_set: false, masked: null },
    anthropic_base_url: "",
    anthropic_model: "",
    anthropic_default_haiku_model: "",
    anthropic_default_opus_model: "",
    anthropic_default_sonnet_model: "",
    claude_code_subagent_model: "",
    agent_session_cleanup_delay_seconds: 0,
    agent_max_concurrent_sessions: 0,
  },
  options: {
    video_backends: ["gemini-aistudio/veo-3"],
    image_backends: ["gemini-aistudio/nano-banana"],
    text_backends: ["gemini-aistudio/g25"],
    audio_backends: ["dashscope/qwen3-tts-flash"],
    provider_names: { "gemini-aistudio": "Gemini AI Studio", dashscope: "DashScope" },
  },
};

/** Global TTS defaults, prefilled unchanged when wizards switch to TTS dubbing. */
const narrationDefaults = {
  audio_backend: "dashscope/qwen3-tts-flash",
  narration_voice: "Cherry",
  narration_speed: 1.1,
};

const mockProviders = {
  providers: [
    {
      id: "gemini-aistudio",
      display_name: "Gemini AI Studio",
      description: "",
      status: "ready" as const,
      media_types: ["video", "image", "text"],
      capabilities: [],
      credential_count: 0,
      models: {
        "veo-3": {
          display_name: "veo-3",
          media_type: "video",
          capabilities: [],
          default: false,
          supported_durations: [4, 6, 8],
        },
      },
    },
  ],
};

/**
 * Projectless endpoint fake: wizard projects do not yet exist; return candidate-model durations already narrowed by servers.
 *
 * Install in every describe beforeEach: `restoreMocks` restores spies after each test,
 * so missing groups call real APIs upon step two.
 */
function stubModelVideoCapabilities() {
  vi.spyOn(API, "getModelVideoCapabilities").mockImplementation((backend) => {
    const durations = backend === "ark/seedance" ? [5, 10] : [4, 6, 8];
    const [provider_id, model] = backend.split("/");
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
      duration_constraints: {
        resolution: null,
        uses_reference_images: false,
        allowed: durations,
        excluded: {},
      },
    });
  });
}

function installStubs(name = "demo-proj") {
  navigateMock.mockClear();
  useProjectsStore.setState(useProjectsStore.getInitialState(), true);
  useProjectsStore.setState({ showCreateModal: true });
  useAppStore.setState(useAppStore.getInitialState(), true);
  vi.spyOn(API, "getSystemConfig").mockResolvedValue(mockSysConfig as never);
  vi.spyOn(API, "getProviders").mockResolvedValue(mockProviders as never);
  vi.spyOn(API, "listCustomProviders").mockResolvedValue({ providers: [] });
  vi.spyOn(API, "getNarrationDefaults").mockResolvedValue(narrationDefaults);
  vi.spyOn(API, "getAutoBackends").mockResolvedValue({ t2i: null, i2i: null, i2v: null, r2v: null, text: null });
  vi.spyOn(API, "getTtsModelCapabilities").mockResolvedValue({ supports_speed: true, voices: [] });
  stubModelVideoCapabilities();
  vi.spyOn(API, "createProject").mockResolvedValue({ success: true, name, project: {} as never });
  vi.spyOn(API, "uploadStyleImage").mockResolvedValue({
    success: true,
    style_image: "",
    style_description: "",
    url: "",
  });
}

const nextButton = () => screen.getByRole("button", { name: /^(下一步|Next)$/ });
const clickNext = () => fireEvent.click(nextButton());

/** Complete step-one required fields: title, creation type and generation mode. */
function fillBasics({ mode = "旁白/解说", route = "分镜图生视频" }: { mode?: string; route?: string } = {}) {
  fireEvent.change(screen.getByRole("textbox", { name: "项目标题" }), { target: { value: "demo" } });
  fireEvent.click(screen.getByRole("radio", { name: new RegExp(mode) }));
  fireEvent.click(screen.getByRole("radio", { name: new RegExp(route) }));
}

/** Advance from step one through three, leaving Create project enabled. */
async function walkToStyle() {
  clickNext();
  await waitFor(() => expect(nextButton()).toBeEnabled());
  clickNext();
  return screen.findByRole("button", { name: "创建项目" });
}

async function createWithDefaults() {
  render(<CreateProjectModal />);
  fillBasics();
  fireEvent.click(await walkToStyle());
  await waitFor(() => expect(API.createProject).toHaveBeenCalled());
  return vi.mocked(API.createProject).mock.calls[0][0];
}

describe("CreateProjectModal", () => {
  beforeEach(() => installStubs());

  it("has no default content mode or generation route and lists what step 1 still needs", () => {
    render(<CreateProjectModal />);
    for (const radio of screen.getAllByRole("radio")) {
      if (/竖屏/.test(radio.closest("label")?.textContent ?? "")) continue;
      expect(radio).not.toBeChecked();
    }
    expect(nextButton()).toBeDisabled();
    expect(nextButton()).toHaveAccessibleDescription("还需要：项目标题、创作类型、生成方式");

    fireEvent.change(screen.getByRole("textbox", { name: "项目标题" }), { target: { value: "demo" } });
    fireEvent.click(screen.getByRole("radio", { name: /剧情演绎/ }));
    expect(nextButton()).toHaveAccessibleDescription("还需要：生成方式");

    fireEvent.click(screen.getByRole("radio", { name: /参考生视频/ }));
    expect(nextButton()).toBeEnabled();
    expect(nextButton()).not.toHaveAccessibleDescription();
  });

  it("marks finished steps in the step indicator as the wizard advances", async () => {
    render(<CreateProjectModal />);
    const steps = within(screen.getByRole("list", { name: "创建步骤" }));
    expect(steps.getByText("基础信息").closest("li")).toHaveAttribute("aria-current", "step");
    fillBasics();
    clickNext();
    await waitFor(() => expect(steps.getByText("生成设置").closest("li")).toHaveAttribute("aria-current", "step"));
    expect(steps.getByText("（已完成）")).toBeInTheDocument();
  });

  it("creates the project only on the third step and opens it", async () => {
    const payload = await createWithDefaults();
    expect(payload).toEqual(
      expect.objectContaining({
        title: "demo",
        content_mode: "narration",
        aspect_ratio: "9:16",
        generation_mode: "storyboard",
        grid_storyboard: false,
        style_template_id: "live_premium_drama",
        video_backend: null,
        default_image_backend: null,
        default_duration: null,
        narration_delivery: "post_production",
      }),
    );
    // Postproduction dubbing omits TTS snapshots and unset optional duration/speech-rate keys.
    for (const key of ["audio_backend", "target_duration", "episode_target_duration", "speech_rate_units_per_second"]) {
      expect(payload).not.toHaveProperty(key);
    }
    expect(navigateMock).toHaveBeenCalledWith("/app/projects/demo-proj");
    expect(useProjectsStore.getState().showCreateModal).toBe(false);
  });

  it("closes from the cancel button", () => {
    render(<CreateProjectModal />);
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(useProjectsStore.getState().showCreateModal).toBe(false);
  });

  it("ignores close requests while the project is being created", async () => {
    let resolveCreate: (value: Awaited<ReturnType<typeof API.createProject>>) => void = () => {};
    vi.spyOn(API, "createProject").mockImplementation(
      () => new Promise((resolve) => (resolveCreate = resolve)),
    );
    render(<CreateProjectModal />);
    fillBasics();
    fireEvent.click(await walkToStyle());
    await waitFor(() => expect(API.createProject).toHaveBeenCalled());

    fireEvent.keyDown(document.activeElement ?? document.body, { key: "Escape" });
    expect(screen.getByRole("button", { name: "取消" })).toBeDisabled();
    expect(useProjectsStore.getState().showCreateModal).toBe(true);

    await act(async () => resolveCreate({ success: true, name: "demo-proj", project: {} as never }));
    expect(navigateMock).toHaveBeenCalledWith("/app/projects/demo-proj");
  });

  it("prefills TTS narration with the global defaults and submits that snapshot", async () => {
    render(<CreateProjectModal />);
    fillBasics();
    clickNext();
    fireEvent.click(await screen.findByRole("radio", { name: "TTS 配音" }));

    expect(screen.getByLabelText("旁白音色 ID")).toHaveValue("Cherry");
    expect(screen.getByLabelText("配音语速（可选）")).toHaveValue(1.1);
    clickNext();
    fireEvent.click(await screen.findByRole("button", { name: "创建项目" }));

    await waitFor(() =>
      expect(API.createProject).toHaveBeenCalledWith(
        expect.objectContaining({
          narration_delivery: "use_tts",
          audio_backend: "dashscope/qwen3-tts-flash",
          narration_voice: "Cherry",
          narration_speed: 1.1,
        }),
      ),
    );
  });

  it("requires a TTS model before leaving step 2 with TTS narration", async () => {
    vi.spyOn(API, "getNarrationDefaults").mockRejectedValue(new Error("boom"));
    render(<CreateProjectModal />);
    fillBasics();
    clickNext();
    fireEvent.click(await screen.findByRole("radio", { name: "TTS 配音" }));

    expect(nextButton()).toBeDisabled();
    expect(nextButton()).toHaveAccessibleDescription("还需要：TTS 模型");

    fireEvent.click(screen.getByRole("radio", { name: "后期配音" }));
    expect(nextButton()).toBeEnabled();
  });

  it("lets step 2 continue with global defaults when the model catalog cannot be read", async () => {
    vi.spyOn(API, "getSystemConfig").mockRejectedValue(new Error("network down"));
    render(<CreateProjectModal />);
    fillBasics();
    clickNext();
    expect(await screen.findByText("network down")).toBeInTheDocument();
    expect(nextButton()).toBeEnabled();
  });

  it("asks the no-project endpoint for the candidate model and lists its narrowed durations", async () => {
    vi.spyOn(API, "getSystemConfig").mockResolvedValue({
      ...mockSysConfig,
      settings: { ...mockSysConfig.settings, default_video_backend: "gemini-aistudio/veo-3" },
    } as never);
    vi.spyOn(API, "getModelVideoCapabilities").mockResolvedValue({
      provider_id: "gemini-aistudio",
      model: "veo-3",
      supported_durations: [8],
      max_duration: 8,
      max_reference_images: 3,
      first_frame: true,
      last_frame: true,
      source: "registry",
      voice_consistency: "soft",
      duration_constraints: { resolution: null, uses_reference_images: true, allowed: [8], excluded: {} },
    } as never);
    render(<CreateProjectModal />);
    fillBasics({ route: "参考生视频" });
    clickNext();
    // Projects do not exist yet; use projectless endpoints with global-default resolved candidates/reference paths.
    await waitFor(() =>
      expect(API.getModelVideoCapabilities).toHaveBeenCalledWith(
        "gemini-aistudio/veo-3",
        expect.objectContaining({ usesReferenceImages: true, resolution: null }),
      ),
    );
    expect(await screen.findByRole("radio", { name: "8 秒" })).toBeInTheDocument();
    expect(screen.queryByRole("radio", { name: "4 秒" })).not.toBeInTheDocument();
  });

  it("submits grid_storyboard only while the storyboard route keeps it on", async () => {
    render(<CreateProjectModal />);
    fillBasics();
    fireEvent.click(screen.getByRole("switch", { name: "多宫格分镜" }));
    // Switching to reference video clears switches, retaining off when switching back.
    fireEvent.click(screen.getByRole("radio", { name: /参考生视频/ }));
    expect(screen.queryByRole("switch", { name: "多宫格分镜" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("radio", { name: /分镜图生视频/ }));
    expect(screen.getByRole("switch", { name: "多宫格分镜" })).not.toBeChecked();
    fireEvent.click(screen.getByRole("switch", { name: "多宫格分镜" }));

    fireEvent.click(await walkToStyle());
    await waitFor(() =>
      expect(API.createProject).toHaveBeenCalledWith(
        expect.objectContaining({ generation_mode: "storyboard", grid_storyboard: true }),
      ),
    );
  });

  it("hides the grid toggle for ad projects and clears it when switching to ad", () => {
    render(<CreateProjectModal />);
    fillBasics();
    fireEvent.click(screen.getByRole("switch", { name: "多宫格分镜" }));
    fireEvent.click(screen.getByRole("radio", { name: /广告\/短片/ }));
    expect(screen.queryByRole("switch", { name: "多宫格分镜" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("radio", { name: /旁白\/解说/ }));
    expect(screen.getByRole("switch", { name: "多宫格分镜" })).not.toBeChecked();
  });

  it("submits the speech rate and episode target duration filled on step 2", async () => {
    render(<CreateProjectModal />);
    fillBasics();
    clickNext();
    fireEvent.change(await screen.findByLabelText(/^语速（可选）$/), { target: { value: "6" } });
    fireEvent.change(screen.getByLabelText(/单集目标时长/), { target: { value: "120" } });
    await waitFor(() => expect(nextButton()).toBeEnabled());
    clickNext();
    fireEvent.click(await screen.findByRole("button", { name: "创建项目" }));
    await waitFor(() =>
      expect(API.createProject).toHaveBeenCalledWith(
        expect.objectContaining({ speech_rate_units_per_second: 6, episode_target_duration: 120 }),
      ),
    );
  });

  it("blocks step 2 and names the fields to fix while durations are out of range", async () => {
    render(<CreateProjectModal />);
    fillBasics();
    clickNext();
    fireEvent.change(await screen.findByLabelText(/^语速（可选）$/), { target: { value: "99" } });
    fireEvent.change(screen.getByLabelText(/单集目标时长/), { target: { value: "5" } });
    expect(nextButton()).toBeDisabled();
    expect(nextButton()).toHaveAccessibleDescription("请修正：单集目标时长、语速");
  });

  it("goes back to step 1 with the entered basics kept", async () => {
    render(<CreateProjectModal />);
    fillBasics();
    clickNext();
    fireEvent.click(await screen.findByRole("button", { name: "上一步" }));
    expect(screen.getByRole("textbox", { name: "项目标题" })).toHaveValue("demo");
    expect(screen.getByRole("radio", { name: /旁白\/解说/ })).toBeChecked();
    expect(nextButton()).toBeEnabled();
  });

  it("revalidates duration and resolution when step 1 switches the executing video model", async () => {
    // Distinct global video-path models mean generation-mode changes switch execution identities; duration/resolution chosen for previous
    // models must clear instead of persisting beneath new models.
    vi.spyOn(API, "getSystemConfig").mockResolvedValue({
      ...mockSysConfig,
      settings: {
        ...mockSysConfig.settings,
        default_video_backend_i2v: "gemini-aistudio/veo-3",
        default_video_backend_r2v: "ark/seedance",
      },
    } as never);
    vi.spyOn(API, "getProviders").mockResolvedValue({
      providers: [
        {
          id: "gemini-aistudio", display_name: "Gemini AI Studio", description: "", status: "ready" as const,
          media_types: ["video", "image", "text"], capabilities: [], credential_count: 0,
          models: {
            "veo-3": {
              display_name: "veo-3", media_type: "video", capabilities: [], default: false,
              supported_durations: [4, 6, 8],
              resolutions: ["720p", "1080p"],
            },
          },
        },
        {
          id: "ark", display_name: "Ark", description: "", status: "ready" as const,
          media_types: ["video"], capabilities: [], credential_count: 0,
          models: {
            seedance: {
              display_name: "seedance", media_type: "video", capabilities: [], default: false,
              supported_durations: [5, 10],
              resolutions: ["720p"],
            },
          },
        },
      ],
    } as never);

    render(<CreateProjectModal />);
    fillBasics();
    clickNext();
    // Step two lists i2v execution model veo-3 durations/resolutions.
    fireEvent.click(await screen.findByRole("radio", { name: "4 秒" }));
    fireEvent.change(screen.getByRole("combobox", { name: /分辨率/ }), { target: { value: "1080p" } });
    fireEvent.click(screen.getByRole("button", { name: "上一步" }));
    fireEvent.click(screen.getByRole("radio", { name: /参考生视频/ }));
    fireEvent.click(await walkToStyle());
    // Switching execution to seedance excludes unsupported 4-second/1080p values from payloads.
    await waitFor(() =>
      expect(API.createProject).toHaveBeenCalledWith(
        expect.objectContaining({ generation_mode: "reference_video", default_duration: null }),
      ),
    );
    const payload = vi.mocked(API.createProject).mock.calls[0][0] as { model_settings?: Record<string, unknown> };
    expect(payload.model_settings).toBeUndefined();
  });

  it("shows an error toast and stays on step 3 when createProject fails", async () => {
    vi.spyOn(API, "createProject").mockRejectedValueOnce(new Error("boom"));
    render(<CreateProjectModal />);
    fillBasics();
    fireEvent.click(await walkToStyle());
    await waitFor(() => expect(useAppStore.getState().toast?.text).toBe("创建项目失败：boom"));
    expect(navigateMock).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "创建项目" })).toBeEnabled();
  });

  it("uploads the custom style image after creating the project", async () => {
    render(<CreateProjectModal />);
    fillBasics();
    const create = await walkToStyle();

    fireEvent.click(screen.getByRole("tab", { name: "自定义" }));
    const file = new File(["content"], "style.png", { type: "image/png" });
    const fileInput = document.querySelector("input[type='file']") as HTMLInputElement;
    Object.defineProperty(fileInput, "files", { value: [file], configurable: true });
    fireEvent.change(fileInput);
    fireEvent.click(create);

    await waitFor(() =>
      expect(API.createProject).toHaveBeenCalledWith(expect.objectContaining({ style_template_id: null })),
    );
    await waitFor(() => expect(API.uploadStyleImage).toHaveBeenCalledWith("demo-proj", file));
  });

  it("keeps the uploaded style preview across leaving and returning to step 3, and frees every preview it stops using", async () => {
    let seq = 0;
    const created = vi.spyOn(URL, "createObjectURL").mockImplementation(() => `blob:preview-${++seq}`);
    const revoked = vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => {});
    render(<CreateProjectModal />);
    fillBasics();
    await walkToStyle();
    fireEvent.click(screen.getByRole("tab", { name: "自定义" }));
    const fileInput = document.querySelector("input[type='file']") as HTMLInputElement;
    Object.defineProperty(fileInput, "files", { value: [new File(["content"], "style.png", { type: "image/png" })] });
    fireEvent.change(fileInput);
    const preview = () => screen.getByRole("img", { name: "上传风格参考图" }).getAttribute("src");
    expect(preview()).toBe("blob:preview-1");

    fireEvent.click(screen.getByRole("button", { name: "上一步" }));
    clickNext();
    await screen.findByRole("button", { name: "创建项目" });

    const shown = preview();
    expect(shown).not.toBeNull();
    expect(revoked).not.toHaveBeenCalledWith(shown);
    // All hidden preview URLs are revoked; only the displayed one exists at any moment.
    const stale = created.mock.results.map((r) => r.value as string).filter((url) => url !== shown);
    expect(revoked.mock.calls.map(([url]) => url).sort()).toEqual(stale.sort());
    created.mockRestore();
    revoked.mockRestore();
  });

  it("creates without a style when the custom tab has no image", async () => {
    render(<CreateProjectModal />);
    fillBasics();
    const create = await walkToStyle();
    fireEvent.click(screen.getByRole("tab", { name: "自定义" }));
    fireEvent.click(create);
    await waitFor(() =>
      expect(API.createProject).toHaveBeenCalledWith(expect.objectContaining({ style_template_id: null })),
    );
    expect(API.uploadStyleImage).not.toHaveBeenCalled();
  });
});

describe("CreateProjectModal ad mode", () => {
  beforeEach(() => installStubs("ad-proj"));

  it("submits the chosen target duration and no default duration", async () => {
    render(<CreateProjectModal />);
    fillBasics({ mode: "广告\\/短片" });
    clickNext();
    fireEvent.click(await screen.findByRole("radio", { name: "30 秒" }));
    expect(screen.queryByLabelText(/单集目标时长/)).not.toBeInTheDocument();
    await waitFor(() => expect(nextButton()).toBeEnabled());
    clickNext();
    fireEvent.click(await screen.findByRole("button", { name: "创建项目" }));
    await waitFor(() => expect(API.createProject).toHaveBeenCalled());

    const payload = vi.mocked(API.createProject).mock.calls[0][0];
    expect(payload).toEqual(expect.objectContaining({ content_mode: "ad", target_duration: 30 }));
    expect(payload).not.toHaveProperty("default_duration");
    expect(navigateMock).toHaveBeenCalledWith("/app/projects/ad-proj");
  });

  it("blocks step 2 until a custom target duration is a positive whole number", async () => {
    render(<CreateProjectModal />);
    fillBasics({ mode: "广告\\/短片" });
    clickNext();
    fireEvent.click(await screen.findByRole("radio", { name: "自定义" }));
    expect(nextButton()).toHaveAccessibleDescription("请修正：目标总时长");
    fireEvent.change(screen.getByRole("spinbutton", { name: "自定义目标总时长（秒）" }), { target: { value: "45" } });
    await waitFor(() => expect(nextButton()).toBeEnabled());
    clickNext();
    fireEvent.click(await screen.findByRole("button", { name: "创建项目" }));
    await waitFor(() =>
      expect(API.createProject).toHaveBeenCalledWith(expect.objectContaining({ target_duration: 45 })),
    );
  });
});

// Backends render provider names by Accept-Language; fakes return matching current-language translations.
function sysConfigFor(lang: string) {
  return {
    ...mockSysConfig,
    settings: { ...mockSysConfig.settings, default_video_backend: "gemini-aistudio/veo-3" },
    options: {
      ...mockSysConfig.options,
      provider_names: {
        "gemini-aistudio": lang === "en" ? "Gemini AI Studio (EN)" : "Gemini AI Studio（中文）",
      },
    },
  };
}

describe("CreateProjectModal language switch", () => {
  beforeEach(() => {
    navigateMock.mockClear();
    useProjectsStore.setState(useProjectsStore.getInitialState(), true);
    useProjectsStore.setState({ showCreateModal: true });
    useAppStore.setState(useAppStore.getInitialState(), true);
    vi.spyOn(API, "getSystemConfig").mockImplementation(() =>
      Promise.resolve(sysConfigFor(i18n.language) as never),
    );
    vi.spyOn(API, "getProviders").mockResolvedValue(mockProviders as never);
    vi.spyOn(API, "listCustomProviders").mockResolvedValue({ providers: [] });
    vi.spyOn(API, "getNarrationDefaults").mockResolvedValue(narrationDefaults);
    vi.spyOn(API, "getTtsModelCapabilities").mockResolvedValue({ supports_speed: true, voices: [] });
    stubModelVideoCapabilities();
  });

  afterEach(async () => {
    await act(async () => {
      await i18n.changeLanguage("zh");
    });
  });

  async function goToStep2() {
    fillBasics();
    clickNext();
    await waitFor(() => expect(screen.getByRole("button", { name: /下一步|Next/ })).toBeEnabled());
  }

  it("renders the model catalog in the new language after the interface language changes", async () => {
    render(<CreateProjectModal />);
    await goToStep2();
    expect(screen.getAllByText(/Gemini AI Studio（中文）/).length).toBeGreaterThan(0);

    await act(async () => {
      await i18n.changeLanguage("en");
    });

    await waitFor(() =>
      expect(screen.getAllByText(/Gemini AI Studio \(EN\)/).length).toBeGreaterThan(0),
    );
    expect(screen.queryByText(/Gemini AI Studio（中文）/)).not.toBeInTheDocument();
  });

  it("keeps step2 usable while the catalog is being refetched", async () => {
    let releaseRefetch: (() => void) | null = null;
    vi.spyOn(API, "getSystemConfig").mockImplementation(() => {
      const lang = i18n.language;
      if (lang !== "en") return Promise.resolve(sysConfigFor(lang) as never);
      return new Promise((resolve) => {
        releaseRefetch = () => resolve(sysConfigFor(lang) as never);
      });
    });

    render(<CreateProjectModal />);
    await goToStep2();

    await act(async () => {
      await i18n.changeLanguage("en");
    });

    // Pending refetches retain existing step2 data; wizards show old catalogs rather than loading.
    expect(screen.getAllByText(/Gemini AI Studio（中文）/).length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: /Next|下一步/ })).toBeEnabled();

    await act(async () => {
      releaseRefetch?.();
      await Promise.resolve();
    });
    await waitFor(() =>
      expect(screen.getAllByText(/Gemini AI Studio \(EN\)/).length).toBeGreaterThan(0),
    );
  });
});
