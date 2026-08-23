import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
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
    vi.spyOn(API, "listCustomStyles").mockResolvedValue({ items: [] });
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
    vi.spyOn(API, "analyzeStyleImageFile").mockResolvedValue({
      success: true,
      style_description: "soft light, muted palette",
    });
    vi.spyOn(API, "saveCustomStyleFromProject").mockResolvedValue({
      style: {
        id: "style-1",
        name: "Demo · 风格",
        description: "soft light, muted palette",
        image_path: null,
        source_project: "demo-proj",
        updated_at: null,
      },
      project: {} as never,
    });
    vi.spyOn(API, "applyCustomStyleToProject").mockResolvedValue({
      style: {
        id: "saved-style",
        name: "韩剧柔光",
        description: "soft k-drama light",
        image_path: "_global_assets/style/kdrama.png",
        source_project: "old-project",
        updated_at: null,
      },
      project: {} as never,
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
    fireEvent.click(screen.getByRole("button", { name: /下一步/ }));
    // Step 2 shows loading or Back button
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /上一步/ })).toBeInTheDocument()
    );
  });

  it("advances from step 2 to step 3 without validation", async () => {
    render(<CreateProjectModal />);
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "demo" } });
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
        content_mode: "drama",
        source_kind: "screenplay",
        aspect_ratio: "16:9",
        generation_mode: "reference_video",
        grid_storyboard: false,
        style_template_id: "live_premium_drama",
        video_backend: null,
        default_image_backend: null,
        default_duration: null,
      })
    );
    expect(navigateMock).toHaveBeenCalledWith("/app/projects/demo-proj");
  });

  it("shows R2V first and selected while I2V remains visible but disabled", () => {
    render(<CreateProjectModal />);
    const group = screen.getByRole("radiogroup", { name: /生成方式|Generation method/ });
    const radios = within(group).getAllByRole("radio");
    expect(radios.map((radio) => radio.getAttribute("value"))).toEqual([
      "reference_video",
      "storyboard",
    ]);
    expect(radios[0]).toBeChecked();
    expect(radios[0]).toBeEnabled();
    expect(radios[1]).toBeDisabled();
  });

  it("does not render or submit a speech-rate field during creation", async () => {
    render(<CreateProjectModal />);
    expect(screen.queryByLabelText(/语速（可选）|Pace \(optional\)/)).not.toBeInTheDocument();
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "demo" } });
    fireEvent.click(screen.getByRole("button", { name: /下一步/ }));
    await waitFor(() => expect(screen.getByRole("button", { name: /下一步/ })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: /下一步/ }));
    fireEvent.click(await screen.findByRole("button", { name: /创建项目/ }));
    await waitFor(() => expect(API.createProject).toHaveBeenCalled());
    expect(vi.mocked(API.createProject).mock.calls[0][0]).not.toHaveProperty(
      "speech_rate_units_per_second",
    );
  });

  it("goes back from step 2 to step 1 preserving title", async () => {
    render(<CreateProjectModal />);
    const titleInput = screen.getByRole("textbox");
    fireEvent.change(titleInput, { target: { value: "demo" } });
    fireEvent.click(screen.getByRole("button", { name: /下一步/ }));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /上一步/ })).toBeInTheDocument()
    );
    fireEvent.click(screen.getByRole("button", { name: /上一步/ }));
    // Back on step 1, title preserved
    expect(screen.getByRole("textbox")).toHaveValue("demo");
  });

  it("shows error toast and stays on step 3 when createProject fails", async () => {
    vi.spyOn(API, "createProject").mockRejectedValueOnce(new Error("boom"));
    render(<CreateProjectModal />);
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "demo" } });
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
    await waitFor(() => expect(API.uploadStyleImage).toHaveBeenCalledWith("demo-proj", file, false));
  });

  it("saves a manually entered style description without requiring an image", async () => {
    render(<CreateProjectModal />);
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "demo" } });
    fireEvent.click(screen.getByRole("button", { name: /下一步/ }));
    await waitFor(() => expect(screen.getByRole("button", { name: /下一步/ })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: /下一步/ }));
    fireEvent.click(await screen.findByRole("button", { name: /自定义|Custom/ }));

    fireEvent.change(screen.getByLabelText(/风格描述|Style description/i), {
      target: { value: "hand-drawn ink, warm paper" },
    });
    fireEvent.click(screen.getByRole("button", { name: /创建项目|Create/i }));

    await waitFor(() => expect(API.createProject).toHaveBeenCalledWith(
      expect.objectContaining({
        style_template_id: null,
        style_description: "hand-drawn ink, warm paper",
      }),
    ));
    expect(API.uploadStyleImage).not.toHaveBeenCalled();
  });

  it("analyzes an uploaded image before creation and lets the returned text drive creation", async () => {
    render(<CreateProjectModal />);
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "demo" } });
    fireEvent.click(screen.getByRole("button", { name: /下一步/ }));
    await waitFor(() => expect(screen.getByRole("button", { name: /下一步/ })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: /下一步/ }));
    fireEvent.click(await screen.findByRole("button", { name: /自定义|Custom/ }));

    const file = new File(["content"], "style.png", { type: "image/png" });
    const fileInput = document.querySelector("input[type='file']") as HTMLInputElement;
    Object.defineProperty(fileInput, "files", { value: [file], configurable: true });
    fireEvent.change(fileInput);
    fireEvent.click(await screen.findByRole("button", { name: /解析风格|Analyze style/i }));

    await waitFor(() => expect(API.analyzeStyleImageFile).toHaveBeenCalledWith(file));
    expect(await screen.findByLabelText(/风格描述|Style description/i)).toHaveValue(
      "soft light, muted palette",
    );
    fireEvent.click(screen.getByRole("button", { name: /创建项目|Create/i }));
    await waitFor(() => expect(API.createProject).toHaveBeenCalledWith(
      expect.objectContaining({ style_description: "soft light, muted palette" }),
    ));
    expect(API.uploadStyleImage).toHaveBeenCalledWith("demo-proj", file, false);
    expect(API.saveCustomStyleFromProject).toHaveBeenCalledWith("demo-proj");
  });

  it("creates a project from a saved custom style card", async () => {
    vi.mocked(API.listCustomStyles).mockResolvedValueOnce({
      items: [{
        id: "saved-style",
        name: "韩剧柔光",
        description: "soft k-drama light",
        image_path: "_global_assets/style/kdrama.png",
        source_project: "old-project",
        updated_at: null,
      }],
    });
    render(<CreateProjectModal />);
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "demo" } });
    fireEvent.click(screen.getByRole("button", { name: /下一步/ }));
    await waitFor(() => expect(screen.getByRole("button", { name: /下一步/ })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: /下一步/ }));
    fireEvent.click(await screen.findByRole("button", { name: /自定义|Custom/ }));
    fireEvent.click(await screen.findByRole("button", { name: "韩剧柔光" }));
    fireEvent.click(screen.getByRole("button", { name: /创建项目|Create/i }));

    await waitFor(() => expect(API.createProject).toHaveBeenCalledWith(
      expect.objectContaining({
        style_template_id: null,
        style_preset_id: "saved-style",
        style_description: "soft k-drama light",
      }),
    ));
    expect(API.applyCustomStyleToProject).toHaveBeenCalledWith("saved-style", "demo-proj");
    expect(API.uploadStyleImage).not.toHaveBeenCalled();
    expect(API.saveCustomStyleFromProject).not.toHaveBeenCalled();
  });

  it("允许在 custom tab 未上传文件时创建项目（风格为可选）", async () => {
    render(<CreateProjectModal />);
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "demo" } });
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
    vi.spyOn(API, "listCustomStyles").mockResolvedValue({ items: [] });
    vi.spyOn(API, "createProject").mockResolvedValue({
      success: true,
      name: "ad-proj",
      project: {} as never,
    });
  });

  it("submits ad project with target_duration and without default_duration", async () => {
    render(<CreateProjectModal />);
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "ad demo" } });
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
        aspect_ratio: "16:9",
        target_duration: 30,
      })
    );
    const payload = vi.mocked(API.createProject).mock.calls[0][0];
    expect("default_duration" in payload).toBe(false);
    expect(navigateMock).toHaveBeenCalledWith("/app/projects/ad-proj");
  });

  it("does not send target_duration for drama projects", async () => {
    render(<CreateProjectModal />);
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "demo" } });
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
