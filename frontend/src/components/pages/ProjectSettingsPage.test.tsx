import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Router, Route } from "wouter";
import { memoryLocation } from "wouter/memory-location";
import "@/i18n";
import { API } from "@/api";
import * as providerModels from "@/utils/provider-models";
import { useAppStore } from "@/stores/app-store";
import { ProjectSettingsPage } from "@/components/pages/ProjectSettingsPage";
import { LeaveGuardProvider } from "@/components/shared/edit-unit/LeaveGuard";

type ProjectResponse = Awaited<ReturnType<typeof API.getProject>>;
type UpdateResponse = Awaited<ReturnType<typeof API.updateProject>>;

const FAKE_CONFIG = {
  options: {
    video_backends: [],
    image_backends: [],
    text_backends: [],
    audio_backends: ["dashscope/qwen3-tts-flash"],
    provider_names: {},
  },
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

function mockConfig(config: object = FAKE_CONFIG) {
  vi.spyOn(API, "getSystemConfig").mockResolvedValue(config as Awaited<ReturnType<typeof API.getSystemConfig>>);
}

/** 加载项目，保存时按服务端行为回显合并后的项目。 */
function mockProject(project: Record<string, unknown>) {
  const base = { title: "Demo", episodes: [], characters: {}, clues: {}, ...project };
  vi.spyOn(API, "getProject").mockResolvedValue({ project: base, scripts: {} } as unknown as ProjectResponse);
  return vi
    .spyOn(API, "updateProject")
    .mockImplementation(async (_name, patch) => ({ success: true, project: { ...base, ...patch } }) as UpdateResponse);
}

function renderAt(path: string) {
  const location = memoryLocation({ path, record: true });
  return {
    ...render(
      <Router hook={location.hook}>
        <LeaveGuardProvider>
          <Route path="/app/projects/:projectName/settings" component={ProjectSettingsPage} />
        </LeaveGuardProvider>
      </Router>,
    ),
    location,
  };
}

const saveButton = () => screen.getByRole("button", { name: "保存" });
const sidebar = () => screen.getByRole("navigation", { name: "项目设置" });

beforeEach(() => {
  useAppStore.setState(useAppStore.getInitialState(), true);
  vi.restoreAllMocks();
  mockConfig();
  vi.spyOn(API, "getModelCandidates").mockResolvedValue(
    FAKE_CANDIDATES as unknown as Awaited<ReturnType<typeof API.getModelCandidates>>,
  );
  vi.spyOn(providerModels, "getProviderModels").mockResolvedValue([]);
  vi.spyOn(providerModels, "getCustomProviderModels").mockResolvedValue([]);
  vi.spyOn(API, "getAgentProfileStatus").mockResolvedValue({ customized: false, customized_files: [] });
  vi.spyOn(API, "getAgentMemory").mockResolvedValue({
    path: "/projects/demo/.arcreel/memory",
    index: { exists: false, line_count: 0, byte_size: 0, over_limit: false },
    files: [],
  });
  vi.spyOn(API, "getNarrationDefaults").mockResolvedValue({
    audio_backend: "dashscope/qwen3-tts-flash",
    narration_voice: "Ethan",
    narration_speed: 1.2,
  });
  vi.spyOn(API, "getTtsModelCapabilities").mockResolvedValue({ supports_speed: true });
});

describe("ProjectSettingsPage – 分页与一次保存", () => {
  it("没有 tab 参数时落在「基础」，侧栏分「项目」「Agent」两组", async () => {
    mockProject({});
    renderAt("/app/projects/demo/settings");

    expect(await screen.findByRole("heading", { level: 2, name: "基础" })).toBeInTheDocument();
    expect(within(sidebar()).getByRole("link", { name: "基础" })).toHaveAttribute("aria-current", "page");
    expect(within(sidebar()).getByRole("list", { name: "项目" })).toBeInTheDocument();
    expect(within(sidebar()).getByRole("list", { name: "Agent" })).toBeInTheDocument();
  });

  it("在表单分页之间切换不拦截、修改保留，侧栏标出有修改的分页；一次保存把字段与风格一起提交", async () => {
    const updateSpy = mockProject({ style_template_id: "live_premium_drama" });
    const { location } = renderAt("/app/projects/demo/settings?tab=basics");

    fireEvent.click(await screen.findByRole("radio", { name: /横屏 16:9/ }));
    fireEvent.click(within(sidebar()).getByRole("link", { name: /^风格/ }));
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(location.history.at(-1)).toBe("/app/projects/demo/settings?tab=style");

    expect(screen.getByText("精品短剧")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "更换" }));
    const dialog = await screen.findByRole("dialog", { name: "更换风格" });
    fireEvent.click(within(dialog).getByRole("button", { name: /张艺谋/ }));
    fireEvent.click(within(dialog).getByRole("button", { name: "使用此风格" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(screen.getByText("张艺谋风格")).toBeInTheDocument();
    expect(updateSpy).not.toHaveBeenCalled();

    expect(within(sidebar()).getByRole("link", { name: /^基础.*有未保存的修改/ })).toBeInTheDocument();
    expect(within(sidebar()).getByRole("link", { name: /^风格.*有未保存的修改/ })).toBeInTheDocument();
    expect(within(sidebar()).getByRole("link", { name: "模型" })).toBeInTheDocument();

    fireEvent.click(saveButton());
    await waitFor(() => expect(updateSpy).toHaveBeenCalledTimes(1));
    expect(updateSpy).toHaveBeenCalledWith(
      "demo",
      expect.objectContaining({ aspect_ratio: "16:9", style_template_id: "live_zhang_yimou" }),
    );
    await waitFor(() => expect(within(sidebar()).getByRole("link", { name: "风格" })).toBeInTheDocument());
  });

  it("有未保存修改时切到 Agent 组的分页会拦截，放弃修改后再切过去", async () => {
    const updateSpy = mockProject({});
    const { location } = renderAt("/app/projects/demo/settings");

    fireEvent.click(await screen.findByRole("radio", { name: /横屏 16:9/ }));
    fireEvent.click(within(sidebar()).getByRole("link", { name: "项目记忆" }));

    const dialog = await screen.findByRole("alertdialog", { name: "有未保存的修改" });
    expect(location.history.at(-1)).toBe("/app/projects/demo/settings");
    fireEvent.click(within(dialog).getByRole("button", { name: "放弃修改" }));

    await waitFor(() => expect(location.history.at(-1)).toBe("/app/projects/demo/settings?tab=memory"));
    expect(await screen.findByText("/projects/demo/.arcreel/memory")).toBeInTheDocument();
    expect(API.getAgentMemory).toHaveBeenCalledWith({ level: "project", projectName: "demo" }, expect.anything());
    // 记忆与 Agent 配置各管各的保存，不显示外壳保存栏
    expect(screen.queryByRole("button", { name: "保存" })).not.toBeInTheDocument();
    expect(updateSpy).not.toHaveBeenCalled();
  });

  it("返回项目前拦截未保存的修改，保存并离开会同时保存设置与风格", async () => {
    const updateSpy = mockProject({ style_template_id: "live_premium_drama" });
    const { location } = renderAt("/app/projects/demo/settings?tab=style");

    fireEvent.click(await screen.findByRole("button", { name: "清除风格" }));
    expect(screen.getByText("未设置风格")).toBeInTheDocument();
    fireEvent.click(within(sidebar()).getByRole("link", { name: /^基础/ }));
    fireEvent.click(await screen.findByRole("radio", { name: /横屏 16:9/ }));
    fireEvent.click(screen.getByRole("button", { name: "返回" }));

    const dialog = await screen.findByRole("alertdialog", { name: "有未保存的修改" });
    fireEvent.click(within(dialog).getByRole("button", { name: "保存并离开" }));

    await waitFor(() => expect(location.history.at(-1)).toBe("/app/projects/demo"));
    expect(updateSpy).toHaveBeenCalledTimes(1);
    expect(updateSpy).toHaveBeenCalledWith(
      "demo",
      expect.objectContaining({ aspect_ratio: "16:9", style_template_id: null, clear_style_image: true }),
    );
  });

  it("新参考图在项目 PATCH 之后上传；上传失败时如实说明其他修改已保存，并保留未保存修改", async () => {
    const updateSpy = mockProject({ style_template_id: "live_premium_drama" });
    const uploadSpy = vi.spyOn(API, "uploadStyleImage").mockRejectedValue(new Error("视觉模型不可用"));
    const user = userEvent.setup();
    renderAt("/app/projects/demo/settings?tab=style");

    await user.click(await screen.findByRole("button", { name: "更换" }));
    const dialog = await screen.findByRole("dialog", { name: "更换风格" });
    await user.click(within(dialog).getByRole("tab", { name: /自定义/ }));
    const file = new File(["png"], "ref.png", { type: "image/png" });
    await user.upload(dialog.querySelector<HTMLInputElement>('input[type="file"]')!, file);
    await user.click(within(dialog).getByRole("button", { name: "使用此风格" }));
    expect(await screen.findByText("自定义风格")).toBeInTheDocument();

    await user.click(saveButton());
    expect(await screen.findByRole("alert")).toHaveTextContent(/其他修改已保存，风格参考图上传失败：视觉模型不可用/);
    expect(updateSpy).toHaveBeenCalledTimes(1);
    expect(updateSpy.mock.calls[0][1]).not.toHaveProperty("style_template_id");
    expect(uploadSpy).toHaveBeenCalledWith("demo", file);
    expect(updateSpy.mock.invocationCallOrder[0]).toBeLessThan(uploadSpy.mock.invocationCallOrder[0]);
    expect(within(sidebar()).getByRole("link", { name: /^风格.*有未保存的修改/ })).toBeInTheDocument();
  });

  it("字段校验未通过时保存与「保存并离开」都不提交，指出所在分页并留在原处", async () => {
    const updateSpy = mockProject({ content_mode: "ad", generation_mode: "storyboard", target_duration: 30 });
    const { location } = renderAt("/app/projects/demo/settings");

    const group = await screen.findByRole("radiogroup", { name: "目标总时长" });
    await waitFor(() => expect(within(group).getByRole("radio", { name: "30 秒" })).toBeChecked());
    // 自定义输入留空：目标总时长无效
    fireEvent.click(within(group).getByRole("radio", { name: "自定义" }));
    fireEvent.click(within(sidebar()).getByRole("link", { name: /^模型/ }));
    fireEvent.click(saveButton());
    expect(await screen.findByText(/「基础」中有字段未通过校验/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "返回" }));
    fireEvent.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "保存并离开" }));
    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
    expect(location.history.at(-1)).toBe("/app/projects/demo/settings?tab=models");
    expect(updateSpy).not.toHaveBeenCalled();
  });
});

describe("ProjectSettingsPage – 模型分页的覆盖来源", () => {
  beforeEach(() => mockConfig(FAKE_CONFIG_WITH_DEFAULTS));

  it("覆盖计数、通道徽章随恢复操作实时变化", async () => {
    mockProject({
      video_backend: "gemini/veo-3",
      video_generate_audio: false,
      default_text_backend: "gemini/g25",
    });
    renderAt("/app/projects/demo/settings?tab=models");

    expect(await screen.findByText("本项目覆盖了 3 项全局默认，其余跟随全局。")).toBeInTheDocument();
    expect(within(sidebar()).getByRole("link", { name: /^模型3 项覆盖$/ })).toBeInTheDocument();
    // 图片通道没有覆盖：显示跟随全局与全局默认模型
    expect(screen.getByText("跟随全局 · nano-banana")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "文本模型：恢复全局" }));
    expect(screen.getByText("本项目覆盖了 2 项全局默认，其余跟随全局。")).toBeInTheDocument();
    expect(screen.getByText("跟随全局 · g25")).toBeInTheDocument();
    expect(within(sidebar()).getByRole("link", { name: /^模型2 项覆盖.*有未保存的修改/ })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "全部恢复为全局默认" }));
    expect(screen.getByText("各通道都跟随全局默认。")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /恢复全局/ })).not.toBeInTheDocument();
    expect(screen.getByText("跟随全局 · veo-3")).toBeInTheDocument();
  });

  it("全局也未指定模型时显示自动选择", async () => {
    mockConfig();
    mockProject({});
    renderAt("/app/projects/demo/settings?tab=models");

    expect(await screen.findAllByText("跟随全局 · 自动选择")).toHaveLength(3);
  });

  it("项目没有覆盖时，默认模型下拉显示跟随的全局默认模型", async () => {
    mockProject({});
    renderAt("/app/projects/demo/settings?tab=models");

    const imageTrigger = await screen.findByRole("combobox", { name: /^默认图片模型$/ });
    expect(imageTrigger).toHaveTextContent(/跟随全局默认/);
    expect(imageTrigger).toHaveTextContent(/nano-banana/);
  });

  it("读取细分项覆盖并各自写回对应的键", async () => {
    const updateSpy = mockProject({ video_provider_i2v: "gemini/veo-3" });
    renderAt("/app/projects/demo/settings?tab=models");

    const i2v = await screen.findByRole("combobox", { name: /^图生视频$/ });
    expect(i2v).toHaveTextContent(/veo-3/);
    fireEvent.click(within(sidebar()).getByRole("link", { name: /^基础/ }));
    fireEvent.click(await screen.findByRole("radio", { name: /横屏 16:9/ }));
    fireEvent.click(saveButton());

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
});

describe("ProjectSettingsPage – model_settings resolution", () => {
  const RESOLUTION_PROVIDERS = [
    {
      id: "gemini",
      display_name: "Gemini",
      description: "",
      status: "ready",
      media_types: ["video", "image"],
      capabilities: [],
      credential_count: 0,
      models: {
        "veo-3": {
          display_name: "Veo 3",
          media_type: "video",
          capabilities: [],
          default: true,
          supported_durations: [5, 8],
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
          resolutions: ["720p", "1080p"],
          audio_track: "always_off",
          reference_route_audio_track: "always_off",
          voice_consistency: "none",
        },
      },
    },
  ] as Awaited<ReturnType<typeof providerModels.getProviderModels>>;

  beforeEach(() => mockConfig(FAKE_CONFIG_WITH_DEFAULTS));

  it.each([false, true])("keeps bucket resolution edits consistent through save (shared model: %s)", async (sharedModel) => {
    vi.spyOn(providerModels, "getProviderModels").mockResolvedValue([
      ...["gemini", "ark"].map((id) => ({
        id, display_name: id, description: "", status: "ready", media_types: ["video"],
        capabilities: [], credential_count: 0,
        models: { [id === "gemini" ? "veo-3" : "seedance"]: {
          display_name: id, media_type: "video", capabilities: [], default: true,
          supported_durations: [8], resolutions: ["720p", "1080p"],
          audio_track: "controllable", reference_route_audio_track: "controllable", voice_consistency: "soft",
        } },
      })),
    ] as Awaited<ReturnType<typeof providerModels.getProviderModels>>);
    const updateSpy = mockProject({
      generation_mode: "reference_video",
      video_provider_i2v: "gemini/veo-3",
      video_provider_r2v: sharedModel ? "gemini/veo-3" : "ark/seedance",
      model_settings: {
        "gemini/veo-3": { resolution: "720p" },
        "ark/seedance": { resolution: "1080p" },
      },
    });
    const user = userEvent.setup();
    renderAt("/app/projects/demo/settings?tab=models");
    const i2v = await screen.findByRole("combobox", { name: /图生视频.*分辨率/ });
    const r2v = screen.getByRole("combobox", { name: /参考生视频.*分辨率/ });
    const pick = async (trigger: HTMLElement, value: string) => {
      await user.click(trigger);
      await user.click(await screen.findByRole("option", { name: value }));
    };
    expect(i2v).toHaveTextContent("720p");
    expect(r2v).toHaveTextContent(sharedModel ? "720p" : "1080p");
    await pick(i2v, "1080p");
    expect(i2v).toHaveTextContent("1080p");
    expect(r2v).toHaveTextContent("1080p");
    await pick(r2v, "720p");
    expect(i2v).toHaveTextContent(sharedModel ? "720p" : "1080p");
    expect(r2v).toHaveTextContent("720p");
    // 共用模型时两次修改互相抵消，另改一处让保存可用
    fireEvent.click(within(sidebar()).getByRole("link", { name: /^基础/ }));
    fireEvent.click(await screen.findByRole("radio", { name: /横屏 16:9/ }));
    fireEvent.click(saveButton());
    await waitFor(() => expect(updateSpy).toHaveBeenCalledWith("demo", expect.objectContaining({
      model_settings: {
        "gemini/veo-3": { resolution: sharedModel ? "720p" : "1080p" },
        "ark/seedance": { resolution: sharedModel ? "1080p" : "720p" },
        "gemini/nano-banana": { resolution: null },
      },
    })));
  });

  it("loads existing model_settings resolution into video/image pickers", async () => {
    vi.spyOn(providerModels, "getProviderModels").mockResolvedValue(RESOLUTION_PROVIDERS);
    mockProject({
      video_backend: "gemini/veo-3",
      image_provider_t2i: "gemini/nano-banana",
      image_provider_i2i: "gemini/nano-banana",
      model_settings: {
        "gemini/veo-3": { resolution: "1080p" },
        "gemini/nano-banana": { resolution: "720p" },
      },
    });

    renderAt("/app/projects/demo/settings?tab=models");

    await waitFor(() => {
      const shown = screen.getAllByRole("combobox", { name: /分辨率/ }).map((el) => el.textContent ?? "");
      expect(shown.some((text) => text.includes("1080p"))).toBe(true);
      expect(shown.some((text) => text.includes("720p"))).toBe(true);
    });
  });

  it("reads and writes the image resolution under the executing text-to-image model", async () => {
    // 项目默认层与文生图槽指向不同模型：后端按执行模型查 model_settings，故读写都挂在
    // 文生图槽那个模型上——挂错 key 时用户选的分辨率会被静默忽略，且重载读回旧值。
    const updateSpy = mockProject({
      video_backend: "gemini/veo-3",
      default_image_backend: "gemini/nano-banana",
      image_provider_t2i: "openai/gpt-image",
      model_settings: {
        "gemini/nano-banana": { resolution: "1080p" },
        "openai/gpt-image": { resolution: "720p" },
      },
    });

    renderAt("/app/projects/demo/settings");
    fireEvent.click(await screen.findByRole("radio", { name: /横屏 16:9/ }));
    fireEvent.click(saveButton());

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
});

describe("ProjectSettingsPage – 基础", () => {
  it("falls back to 9:16 aspect ratio highlight when project has no aspect_ratio set", async () => {
    mockProject({});
    renderAt("/app/projects/demo/settings");

    expect(await screen.findByRole("radio", { name: /竖屏 9:16/ })).toBeChecked();
    expect(screen.getByRole("radio", { name: /横屏 16:9/ })).not.toBeChecked();
  });

  it("shows the generation route read-only, with no control to change it", async () => {
    mockProject({ generation_mode: "reference_video" });
    renderAt("/app/projects/demo/settings");

    expect(await screen.findByText(/跳过分镜图，直接用角色、场景、道具图作为参考生成视频/)).toBeInTheDocument();
    expect(screen.getByText(/生成方式创建后不可更改/)).toBeInTheDocument();
    expect(screen.queryByRole("radio", { name: /参考生视频|分镜图生视频/ })).not.toBeInTheDocument();
    // 参考生视频下不呈现宫格开关
    expect(screen.queryByRole("switch", { name: /多宫格分镜/ })).not.toBeInTheDocument();
  });

  it("saves the grid assembly toggle on the storyboard route", async () => {
    const updateSpy = mockProject({ generation_mode: "storyboard", grid_storyboard: false });
    renderAt("/app/projects/demo/settings");

    const toggle = await screen.findByRole("switch", { name: /多宫格分镜/ });
    expect(toggle).not.toBeChecked();
    fireEvent.click(toggle);
    expect(toggle).toBeChecked();

    fireEvent.click(saveButton());
    await waitFor(() => {
      expect(updateSpy).toHaveBeenCalledWith("demo", expect.objectContaining({ grid_storyboard: true }));
    });
    // 生成模式不在 PATCH 面上
    expect(updateSpy.mock.calls[0][1]).not.toHaveProperty("generation_mode");
  });

  it("loads, edits and clears the episode target duration", async () => {
    const updateSpy = mockProject({ generation_mode: "storyboard", episode_target_duration: 90 });
    renderAt("/app/projects/demo/settings");

    const input = await screen.findByLabelText(/单集目标时长/);
    expect(input).toHaveValue(90);

    fireEvent.change(input, { target: { value: "120" } });
    fireEvent.click(saveButton());
    await waitFor(() => {
      expect(updateSpy).toHaveBeenCalledWith("demo", expect.objectContaining({ episode_target_duration: 120 }));
    });

    // 清空即写回 null（后端据此删除该偏好）
    fireEvent.change(screen.getByLabelText(/单集目标时长/), { target: { value: "" } });
    fireEvent.click(saveButton());
    await waitFor(() => {
      expect(updateSpy).toHaveBeenLastCalledWith("demo", expect.objectContaining({ episode_target_duration: null }));
    });
  });

  it("does not save while the episode target duration is out of range", async () => {
    const updateSpy = mockProject({ generation_mode: "storyboard" });
    renderAt("/app/projects/demo/settings");

    fireEvent.change(await screen.findByLabelText(/单集目标时长/), { target: { value: "5" } });
    fireEvent.click(saveButton());
    expect(await screen.findByText(/「基础」中有字段未通过校验/)).toBeInTheDocument();
    expect(updateSpy).not.toHaveBeenCalled();
  });

  it("hides the episode target duration and the grid toggle for ad projects", async () => {
    mockProject({ content_mode: "ad", generation_mode: "storyboard" });
    renderAt("/app/projects/demo/settings");

    expect(await screen.findByText(/先为每个分镜生成分镜图/)).toBeInTheDocument();
    expect(screen.queryByLabelText(/单集目标时长/)).not.toBeInTheDocument();
    expect(screen.queryByRole("switch", { name: /多宫格分镜/ })).not.toBeInTheDocument();
  });

  it("loads and saves the target duration of ad projects", async () => {
    const updateSpy = mockProject({ content_mode: "ad", generation_mode: "storyboard", target_duration: 30 });
    renderAt("/app/projects/demo/settings");

    const group = await screen.findByRole("radiogroup", { name: "目标总时长" });
    await waitFor(() => expect(within(group).getByRole("radio", { name: "30 秒" })).toBeChecked());
    fireEvent.click(within(group).getByRole("radio", { name: "60 秒" }));
    fireEvent.click(saveButton());
    await waitFor(() => {
      expect(updateSpy).toHaveBeenCalledWith("demo", expect.objectContaining({ target_duration: 60 }));
    });
    expect(updateSpy.mock.calls[0][1]).not.toHaveProperty("episode_target_duration");
  });

  it("shows the reading unit of the project source language and saves the speech rate", async () => {
    const updateSpy = mockProject({ source_language: "en", speech_rate_units_per_second: 3 });
    renderAt("/app/projects/demo/settings");

    const input = await screen.findByLabelText(/^语速（可选）$/);
    expect(input).toHaveValue(3);
    // en 项目按词计
    expect(screen.getByText("词/秒")).toBeInTheDocument();

    fireEvent.change(input, { target: { value: "4.5" } });
    fireEvent.click(saveButton());
    await waitFor(() => {
      expect(updateSpy).toHaveBeenCalledWith("demo", expect.objectContaining({ speech_rate_units_per_second: 4.5 }));
    });
  });
});

describe("ProjectSettingsPage – 配音", () => {
  it("saves the character voice binding on the reference-video route", async () => {
    const updateSpy = mockProject({ generation_mode: "reference_video" });
    renderAt("/app/projects/demo/settings?tab=voice");

    // 字段缺省即默认档：提示词约束选中
    expect(await screen.findByRole("radio", { name: /^提示词约束/ })).toBeChecked();
    fireEvent.click(screen.getByRole("radio", { name: /^参考音频/ }));
    fireEvent.click(saveButton());
    await waitFor(() => {
      expect(updateSpy).toHaveBeenCalledWith("demo", expect.objectContaining({ character_voice_binding: "reference_audio" }));
    });
  });

  it("omits the character voice binding on the storyboard route", async () => {
    const updateSpy = mockProject({ generation_mode: "storyboard", character_voice_binding: "reference_audio" });
    renderAt("/app/projects/demo/settings?tab=voice");

    fireEvent.click(await screen.findByRole("radio", { name: "TTS 配音" }));
    expect(screen.queryByRole("radio", { name: /^提示词约束/ })).not.toBeInTheDocument();
    fireEvent.click(saveButton());
    // 参考音频通道只属于参考生视频路线：分镜图生视频下该键与项目无关，不写
    await waitFor(() => expect(updateSpy).toHaveBeenCalled());
    expect(updateSpy.mock.calls[0][1]).not.toHaveProperty("character_voice_binding");
  });

  it("switching a TTS project to post-production writes only the delivery and keeps the snapshot", async () => {
    const updateSpy = mockProject({
      content_mode: "drama",
      narration_delivery: "use_tts",
      audio_backend: "dashscope/qwen3-tts-flash",
      narration_voice: "Cherry",
      narration_speed: 0.9,
    });
    renderAt("/app/projects/demo/settings?tab=voice");

    expect(await screen.findByLabelText("旁白音色 ID")).toHaveValue("Cherry");
    fireEvent.click(screen.getByRole("radio", { name: "后期配音" }));
    fireEvent.click(saveButton());

    await waitFor(() => expect(updateSpy).toHaveBeenCalled());
    const patch = updateSpy.mock.calls[0][1];
    expect(patch).toHaveProperty("narration_delivery", "post_production");
    for (const key of ["audio_backend", "narration_voice", "narration_speed"]) {
      expect(patch).not.toHaveProperty(key);
    }
  });

  it("prefills a project without a snapshot from the global defaults when switched to TTS", async () => {
    const updateSpy = mockProject({ content_mode: "narration", narration_delivery: "post_production" });
    renderAt("/app/projects/demo/settings?tab=voice");

    fireEvent.click(await screen.findByRole("radio", { name: "TTS 配音" }));
    expect(screen.getByLabelText("旁白音色 ID")).toHaveValue("Ethan");
    expect(screen.getByLabelText("配音语速（可选）")).toHaveValue(1.2);
    fireEvent.click(saveButton());

    await waitFor(() => expect(updateSpy).toHaveBeenCalled());
    expect(updateSpy.mock.calls[0][1]).toMatchObject({
      narration_delivery: "use_tts",
      audio_backend: "dashscope/qwen3-tts-flash",
      narration_voice: "Ethan",
      narration_speed: 1.2,
    });
  });

  it("leaves a legacy post-production audio backend untouched when saving other settings", async () => {
    const updateSpy = mockProject({
      content_mode: "narration",
      narration_delivery: "post_production",
      audio_backend: "dashscope",
    });
    renderAt("/app/projects/demo/settings");

    fireEvent.click(await screen.findByRole("switch", { name: /多宫格分镜/ }));
    fireEvent.click(saveButton());

    await waitFor(() => expect(updateSpy).toHaveBeenCalled());
    expect(updateSpy.mock.calls[0][1]).not.toHaveProperty("audio_backend");
  });
});

describe("ProjectSettingsPage – Agent 配置", () => {
  it("shows customized Agent Profile files and resets only after destructive confirmation", async () => {
    mockProject({});
    vi.spyOn(API, "getAgentProfileStatus").mockResolvedValue({
      customized: true,
      customized_files: ["CLAUDE.md", ".claude/agents/legacy.md"],
    });
    const resetSpy = vi.spyOn(API, "resetAgentProfile").mockResolvedValue({ customized: false, customized_files: [] });
    renderAt("/app/projects/demo/settings?tab=agent");

    expect(await screen.findByText("CLAUDE.md")).toBeInTheDocument();
    expect(screen.getByText(".claude/agents/legacy.md")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "重置为内置配置" }));
    const dialog = await screen.findByRole("alertdialog", { name: "重置 Agent 配置？" });
    expect(resetSpy).not.toHaveBeenCalled();
    expect(within(dialog).getByText(/重新连接/)).toBeInTheDocument();

    fireEvent.click(within(dialog).getByRole("button", { name: "确认重置" }));
    await waitFor(() => expect(resetSpy).toHaveBeenCalledWith("demo"));
    expect(await screen.findByText("正在使用内置配置")).toBeInTheDocument();
  });

  it("requires another confirmation when customized files change before reset", async () => {
    mockProject({});
    const initialStatus = { customized: true, customized_files: ["CLAUDE.md"] };
    const changedStatus = { customized: true, customized_files: [".claude/agents/new.md", "CLAUDE.md"] };
    vi.spyOn(API, "getAgentProfileStatus")
      .mockResolvedValueOnce(initialStatus)
      .mockResolvedValueOnce(initialStatus)
      .mockResolvedValue(changedStatus);
    const resetSpy = vi.spyOn(API, "resetAgentProfile");
    renderAt("/app/projects/demo/settings?tab=agent");

    expect(await screen.findByText("CLAUDE.md")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "重置为内置配置" }));
    const dialog = await screen.findByRole("alertdialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "确认重置" }));

    expect(await screen.findAllByText(".claude/agents/new.md")).toHaveLength(2);
    expect(screen.getByRole("alertdialog")).toBeInTheDocument();
    expect(resetSpy).not.toHaveBeenCalled();
  });
});
