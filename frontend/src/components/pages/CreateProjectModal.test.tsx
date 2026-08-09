import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";

// Stub URL object APIs not available in jsdom
globalThis.URL.createObjectURL ??= vi.fn(() => "blob:mock");
globalThis.URL.revokeObjectURL ??= vi.fn();
import "@/i18n";
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
    provider_names: { "gemini-aistudio": "Gemini AI Studio" },
  },
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
        },
      },
    },
  ],
};

describe("CreateProjectModal", () => {
  beforeEach(() => {
    navigateMock.mockClear();
    useProjectsStore.setState(useProjectsStore.getInitialState(), true);
    useProjectsStore.setState({ showCreateModal: true });
    useAppStore.setState(useAppStore.getInitialState(), true);
    vi.spyOn(API, "getSystemConfig").mockResolvedValue(mockSysConfig as never);
    vi.spyOn(API, "getProviders").mockResolvedValue(mockProviders as never);
    vi.spyOn(API, "listCustomProviders").mockResolvedValue({ providers: [] });
    vi.spyOn(API, "createProject").mockResolvedValue({
      success: true,
      name: "demo-proj",
      project: {} as never,
    });
    vi.spyOn(API, "uploadStyleImage").mockResolvedValue({
      success: true,
      style_image: "",
      style_description: "",
      url: "",
    });
  });

  it("starts at step 1 and shows title input", () => {
    render(<CreateProjectModal />);
    expect(screen.getByRole("textbox")).toBeInTheDocument();
    // Next button disabled until title typed
    expect(screen.getByRole("button", { name: /下一步/ })).toBeDisabled();
  });

  it("advances from step 1 to step 2 after title entered and Next clicked", async () => {
    render(<CreateProjectModal />);
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "demo" } });
    // 路线无预选、必选：不选则 Next 恒禁用
    fireEvent.click(screen.getByRole("radio", { name: /分镜图生视频/ }));
    fireEvent.click(screen.getByRole("button", { name: /下一步/ }));
    // Step 2 shows loading or Back button
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /上一步/ })).toBeInTheDocument()
    );
  });

  it("advances from step 2 to step 3 without validation", async () => {
    render(<CreateProjectModal />);
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "demo" } });
    // 路线无预选、必选：不选则 Next 恒禁用
    fireEvent.click(screen.getByRole("radio", { name: /分镜图生视频/ }));
    fireEvent.click(screen.getByRole("button", { name: /下一步/ })); // to step 2
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /下一步/ })).toBeEnabled()
    );
    fireEvent.click(screen.getByRole("button", { name: /下一步/ }));
    // Step 3: Create button appears
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /创建项目/ })).toBeInTheDocument()
    );
  });

  it("submits createProject with default template when Create clicked on step 3", async () => {
    render(<CreateProjectModal />);
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "demo" } });
    // 路线无预选、必选：不选则 Next 恒禁用
    fireEvent.click(screen.getByRole("radio", { name: /分镜图生视频/ }));
    fireEvent.click(screen.getByRole("button", { name: /下一步/ }));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /下一步/ })).toBeEnabled()
    );
    fireEvent.click(screen.getByRole("button", { name: /下一步/ }));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /创建项目/ })).toBeInTheDocument()
    );
    fireEvent.click(screen.getByRole("button", { name: /创建项目/ }));
    await waitFor(() => expect(API.createProject).toHaveBeenCalled());
    expect(API.createProject).toHaveBeenCalledWith(
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
      })
    );
    expect(navigateMock).toHaveBeenCalledWith("/app/projects/demo-proj");
  });

  it("submits grid_storyboard when the assembly toggle is switched on at creation", async () => {
    render(<CreateProjectModal />);
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "demo" } });
    fireEvent.click(screen.getByRole("radio", { name: /分镜图生视频/ }));
    fireEvent.click(screen.getByRole("switch", { name: /分镜板（宫格）生视频/ }));
    fireEvent.click(screen.getByRole("button", { name: /下一步/ }));
    await waitFor(() => expect(screen.getByRole("button", { name: /下一步/ })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: /下一步/ }));
    fireEvent.click(await screen.findByRole("button", { name: /创建项目/ }));
    await waitFor(() =>
      expect(API.createProject).toHaveBeenCalledWith(
        expect.objectContaining({ generation_mode: "storyboard", grid_storyboard: true }),
      ),
    );
  });

  it("omits the speech rate when left empty and submits it when filled", async () => {
    const { unmount } = render(<CreateProjectModal />);
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "demo" } });
    fireEvent.click(screen.getByRole("radio", { name: /分镜图生视频/ }));
    fireEvent.click(screen.getByRole("button", { name: /下一步/ }));
    await waitFor(() => expect(screen.getByRole("button", { name: /下一步/ })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: /下一步/ }));
    fireEvent.click(await screen.findByRole("button", { name: /创建项目/ }));
    await waitFor(() => expect(API.createProject).toHaveBeenCalled());
    // 未填不带该键：服务端不落盘，估算回退语言默认
    expect(vi.mocked(API.createProject).mock.calls[0][0]).not.toHaveProperty(
      "speech_rate_units_per_second",
    );
    unmount();

    vi.mocked(API.createProject).mockClear();
    render(<CreateProjectModal />);
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "demo" } });
    fireEvent.click(screen.getByRole("radio", { name: /分镜图生视频/ }));
    fireEvent.change(screen.getByLabelText(/口播语速估算/), { target: { value: "6" } });
    fireEvent.click(screen.getByRole("button", { name: /下一步/ }));
    await waitFor(() => expect(screen.getByRole("button", { name: /下一步/ })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: /下一步/ }));
    fireEvent.click(await screen.findByRole("button", { name: /创建项目/ }));
    await waitFor(() =>
      expect(API.createProject).toHaveBeenCalledWith(
        expect.objectContaining({ speech_rate_units_per_second: 6 }),
      ),
    );
  });

  it("goes back from step 2 to step 1 preserving title", async () => {
    render(<CreateProjectModal />);
    const titleInput = screen.getByRole("textbox");
    fireEvent.change(titleInput, { target: { value: "demo" } });
    fireEvent.click(screen.getByRole("radio", { name: /分镜图生视频/ }));
    fireEvent.click(screen.getByRole("button", { name: /下一步/ }));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /上一步/ })).toBeInTheDocument()
    );
    fireEvent.click(screen.getByRole("button", { name: /上一步/ }));
    // Back on step 1, title preserved
    expect(screen.getByRole("textbox")).toHaveValue("demo");
  });

  it("revalidates duration and resolution when step 1 switches the executing video model", async () => {
    // 全局给两条视频路径指定了不同模型时，改生成模式即换执行模型：时长与分辨率都是按前一个
    // 模型选的，不清掉会被写到新模型名下
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
          media_types: ["video", "image", "text"], capabilities: [], configured_keys: [], missing_keys: [],
          models: {
            "veo-3": {
              display_name: "veo-3", media_type: "video", capabilities: [], default: false,
              supported_durations: [4, 6, 8], duration_resolution_constraints: {},
              resolutions: ["720p", "1080p"],
            },
          },
        },
        {
          id: "ark", display_name: "Ark", description: "", status: "ready" as const,
          media_types: ["video"], capabilities: [], configured_keys: [], missing_keys: [],
          models: {
            seedance: {
              display_name: "seedance", media_type: "video", capabilities: [], default: false,
              supported_durations: [5, 10], duration_resolution_constraints: {},
              resolutions: ["720p"],
            },
          },
        },
      ],
    } as never);

    render(<CreateProjectModal />);
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "demo" } });
    // 路线无预选、必选：不选则 Next 恒禁用
    fireEvent.click(screen.getByRole("radio", { name: /分镜图生视频/ }));
    fireEvent.click(screen.getByRole("button", { name: /下一步/ }));
    // 第二步按 i2v 执行模型（veo-3）列时长与分辨率
    fireEvent.click(await screen.findByRole("radio", { name: "4 秒" }));
    fireEvent.change(screen.getByRole("combobox", { name: /分辨率/ }), { target: { value: "1080p" } });
    fireEvent.click(screen.getByRole("button", { name: /上一步/ }));
    fireEvent.click(screen.getByRole("radio", { name: /参考生视频/ }));
    fireEvent.click(screen.getByRole("button", { name: /下一步/ }));
    await waitFor(() => expect(screen.getByRole("button", { name: /下一步/ })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: /下一步/ }));
    fireEvent.click(await screen.findByRole("button", { name: /创建项目/ }));
    // 执行模型换成 seedance：4 秒不在其支持集内、1080p 也不是它的分辨率，两者都不跟进载荷
    await waitFor(() =>
      expect(API.createProject).toHaveBeenCalledWith(
        expect.objectContaining({ generation_mode: "reference_video", default_duration: null }),
      ),
    );
    const payload = vi.mocked(API.createProject).mock.calls[0][0] as { model_settings?: Record<string, unknown> };
    expect(payload.model_settings).toBeUndefined();
  });

  it("shows error toast and stays on step 3 when createProject fails", async () => {
    vi.spyOn(API, "createProject").mockRejectedValueOnce(new Error("boom"));
    render(<CreateProjectModal />);
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "demo" } });
    // 路线无预选、必选：不选则 Next 恒禁用
    fireEvent.click(screen.getByRole("radio", { name: /分镜图生视频/ }));
    fireEvent.click(screen.getByRole("button", { name: /下一步/ }));
    await waitFor(() => expect(screen.getByRole("button", { name: /下一步/ })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: /下一步/ }));
    await waitFor(() => expect(screen.getByRole("button", { name: /创建项目/ })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: /创建项目/ }));
    await waitFor(() => expect(API.createProject).toHaveBeenCalled());
    // Not navigated away
    expect(navigateMock).not.toHaveBeenCalled();
    // Create button re-enabled after failure (creating=false)
    await waitFor(() => expect(screen.getByRole("button", { name: /创建项目/ })).toBeEnabled());
  });

  it("calls uploadStyleImage after createProject when in custom mode with uploaded file", async () => {
    render(<CreateProjectModal />);
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "demo" } });
    // 路线无预选、必选：不选则 Next 恒禁用
    fireEvent.click(screen.getByRole("radio", { name: /分镜图生视频/ }));
    fireEvent.click(screen.getByRole("button", { name: /下一步/ }));
    await waitFor(() => expect(screen.getByRole("button", { name: /下一步/ })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: /下一步/ }));
    await waitFor(() => expect(screen.getByRole("button", { name: /创建项目/ })).toBeInTheDocument());

    // Switch to custom tab
    fireEvent.click(screen.getByRole("button", { name: /自定义|Custom/ }));
    // Upload a file via the hidden file input
    const file = new File(["content"], "style.png", { type: "image/png" });
    const fileInput = document.querySelector("input[type='file']") as HTMLInputElement;
    Object.defineProperty(fileInput, "files", { value: [file], configurable: true });
    fireEvent.change(fileInput);

    await waitFor(() => expect(screen.getByRole("button", { name: /创建项目/ })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: /创建项目/ }));

    await waitFor(() => expect(API.createProject).toHaveBeenCalled());
    expect(API.createProject).toHaveBeenCalledWith(expect.objectContaining({
      style_template_id: null,
    }));
    await waitFor(() => expect(API.uploadStyleImage).toHaveBeenCalledWith("demo-proj", file));
  });

  it("允许在 custom tab 未上传文件时创建项目（风格为可选）", async () => {
    render(<CreateProjectModal />);
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "demo" } });
    // 路线无预选、必选：不选则 Next 恒禁用
    fireEvent.click(screen.getByRole("radio", { name: /分镜图生视频/ }));
    fireEvent.click(screen.getByRole("button", { name: /下一步/ }));
    await waitFor(() => expect(screen.getByRole("button", { name: /下一步/ })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: /下一步/ }));
    await waitFor(() => expect(screen.getByRole("button", { name: /创建项目/ })).toBeInTheDocument());

    // Switch to custom tab WITHOUT uploading anything
    fireEvent.click(screen.getByRole("button", { name: /自定义|Custom/ }));

    // Create button should still be enabled — style is optional
    await waitFor(() => expect(screen.getByRole("button", { name: /创建项目/ })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: /创建项目/ }));

    await waitFor(() => expect(API.createProject).toHaveBeenCalled());
    expect(API.createProject).toHaveBeenCalledWith(expect.objectContaining({
      style_template_id: null,
    }));
    // No upload since no file
    expect(API.uploadStyleImage).not.toHaveBeenCalled();
  });
});

describe("CreateProjectModal ad mode", () => {
  beforeEach(() => {
    navigateMock.mockClear();
    useProjectsStore.setState(useProjectsStore.getInitialState(), true);
    useProjectsStore.setState({ showCreateModal: true });
    useAppStore.setState(useAppStore.getInitialState(), true);
    vi.spyOn(API, "getSystemConfig").mockResolvedValue(mockSysConfig as never);
    vi.spyOn(API, "getProviders").mockResolvedValue(mockProviders as never);
    vi.spyOn(API, "listCustomProviders").mockResolvedValue({ providers: [] });
    vi.spyOn(API, "createProject").mockResolvedValue({
      success: true,
      name: "ad-proj",
      project: {} as never,
    });
  });

  it("submits ad project with target_duration and without default_duration", async () => {
    render(<CreateProjectModal />);
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "ad demo" } });
    fireEvent.click(screen.getByRole("radio", { name: /分镜图生视频/ }));
    fireEvent.click(screen.getByText(/广告\/短片/));
    // 改选 30 秒档
    fireEvent.click(screen.getByRole("radio", { name: /30\s*秒/ }));
    fireEvent.click(screen.getByRole("button", { name: /下一步/ }));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /下一步/ })).toBeEnabled()
    );
    fireEvent.click(screen.getByRole("button", { name: /下一步/ }));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /创建项目/ })).toBeInTheDocument()
    );
    fireEvent.click(screen.getByRole("button", { name: /创建项目/ }));
    await waitFor(() => expect(API.createProject).toHaveBeenCalled());

    expect(API.createProject).toHaveBeenCalledWith(
      expect.objectContaining({
        title: "ad demo",
        content_mode: "ad",
        aspect_ratio: "9:16",
        target_duration: 30,
      })
    );
    const payload = vi.mocked(API.createProject).mock.calls[0][0];
    expect("default_duration" in payload).toBe(false);
    expect(navigateMock).toHaveBeenCalledWith("/app/projects/ad-proj");
  });

  it("does not send target_duration for narration projects", async () => {
    render(<CreateProjectModal />);
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "demo" } });
    // 路线无预选、必选：不选则 Next 恒禁用
    fireEvent.click(screen.getByRole("radio", { name: /分镜图生视频/ }));
    fireEvent.click(screen.getByRole("button", { name: /下一步/ }));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /下一步/ })).toBeEnabled()
    );
    fireEvent.click(screen.getByRole("button", { name: /下一步/ }));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /创建项目/ })).toBeInTheDocument()
    );
    fireEvent.click(screen.getByRole("button", { name: /创建项目/ }));
    await waitFor(() => expect(API.createProject).toHaveBeenCalled());
    const payload = vi.mocked(API.createProject).mock.calls[0][0];
    expect("target_duration" in payload).toBe(false);
  });
});
