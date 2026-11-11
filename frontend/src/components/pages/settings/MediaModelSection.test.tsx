import { act, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import i18n from "@/i18n";
import { API } from "@/api";
import * as providerModels from "@/utils/provider-models";
import { useAppStore } from "@/stores/app-store";
import { MediaModelSection } from "./MediaModelSection";
import type { ProviderInfo, VideoAudioControl } from "@/types/provider";

const CONFIG = {
  options: {
    video_backends: ["gemini/veo-3", "ark/seedance"],
    image_backends: ["gemini/nano-banana", "openai/gpt-image-edit"],
    text_backends: ["gemini/g25"],
    audio_backends: [],
    provider_names: {},
  },
  settings: {
    default_video_backend: "gemini/veo-3",
    default_image_backend: "gemini/nano-banana",
    default_text_backend: "gemini/g25",
    text_backend_simple: "",
    text_backend_complex: "",
    video_generate_audio: false,
    video_poll_timeout_seconds: 3600,
    market_github_proxy_prefix: "",
  },
};

const CANDIDATES = {
  image: {
    default: ["gemini/nano-banana", "openai/gpt-image-edit"],
    buckets: { t2i: ["gemini/nano-banana"], i2i: ["gemini/nano-banana", "openai/gpt-image-edit"] },
    filtered: {},
  },
  video: {
    default: ["gemini/veo-3", "ark/seedance"],
    buckets: { i2v: ["gemini/veo-3"], r2v: ["ark/seedance"] },
    filtered: {},
  },
  provider_names: {},
};

const NO_AUTO = { t2i: null, i2i: null, i2v: null, r2v: null, text: null };

function mockConfig(settings: Record<string, unknown> = {}) {
  vi.spyOn(API, "getSystemConfig").mockResolvedValue({
    ...CONFIG,
    settings: { ...CONFIG.settings, ...settings },
  } as unknown as Awaited<ReturnType<typeof API.getSystemConfig>>);
}

describe("MediaModelSection", () => {
  beforeEach(() => {
    useAppStore.setState(useAppStore.getInitialState(), true);
    vi.restoreAllMocks();
    mockConfig();
    vi.spyOn(API, "getAutoBackends").mockResolvedValue(NO_AUTO);
    vi.spyOn(API, "getRecommendationChanges").mockResolvedValue({ changes: [] });
    vi.spyOn(API, "getModelCandidates").mockResolvedValue(
      CANDIDATES as unknown as Awaited<ReturnType<typeof API.getModelCandidates>>,
    );
    vi.spyOn(providerModels, "getProviderModels").mockResolvedValue([]);
    vi.spyOn(providerModels, "getCustomProviderModels").mockResolvedValue([]);
  });

  it("gives every media channel a resident default dropdown plus a collapsed per-purpose section", async () => {
    const { container } = render(<MediaModelSection />);
    for (const name of ["默认视频模型", "默认图片模型", "默认模型"]) {
      expect(await screen.findByRole("combobox", { name })).toBeInTheDocument();
    }
    // video / image / text 三处折叠区，初始收起
    const sections = screen.getAllByRole("button", { name: /按用途指定模型/ });
    expect(sections).toHaveLength(3);
    expect(sections.every((d) => d.getAttribute("aria-expanded") === "false")).toBe(true);
    // 界面文案不出现内部术语
    expect(container).not.toHaveTextContent(/能力桶|任务类型桶|capability bucket/i);
  });

  it("shows a changed recommendation under the default model and lets the user switch or keep", async () => {
    const user = userEvent.setup();
    const change = {
      media_type: "video" as const,
      provider_id: "ark",
      recommended: "ark/seedance",
      current: "gemini/veo-3",
    };
    const getChanges = vi
      .spyOn(API, "getRecommendationChanges")
      .mockResolvedValueOnce({ changes: [change] })
      .mockResolvedValue({ changes: [] });
    const acknowledge = vi.spyOn(API, "acknowledgeRecommendation").mockResolvedValue();
    const patch = vi.spyOn(API, "updateSystemConfig");

    render(<MediaModelSection />);

    expect(await screen.findByText("ark 现在推荐 seedance，当前使用 veo-3")).toBeInTheDocument();
    // 只提示，不替用户改：没有经保存栏改动全局默认模型
    expect(patch).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "切换到 seedance" }));

    expect(acknowledge).toHaveBeenCalledWith(change, true);
    // 处理后重取配置与提示，提示随之消失
    await waitFor(() => expect(getChanges).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.queryByText("ark 现在推荐 seedance，当前使用 veo-3")).not.toBeInTheDocument());
  });

  it("keeps a switched recommendation when other unsaved edits are saved afterwards", async () => {
    const user = userEvent.setup();
    const change = {
      media_type: "image" as const,
      provider_id: "openai",
      recommended: "openai/gpt-image-edit",
      current: "gemini/nano-banana",
    };
    vi.spyOn(API, "getRecommendationChanges")
      .mockResolvedValueOnce({ changes: [change] })
      .mockResolvedValue({ changes: [] });
    // 切换由服务端写入全局默认：之后重取的配置带着新的图片默认模型
    vi.spyOn(API, "acknowledgeRecommendation").mockImplementation(async () => {
      mockConfig({ default_image_backend: change.recommended });
    });
    const patch = vi.spyOn(API, "updateSystemConfig").mockResolvedValue(CONFIG as never);
    render(<MediaModelSection />);

    const timeout = await screen.findByRole("textbox", { name: "视频轮询超时（秒）" });
    await user.clear(timeout);
    await user.type(timeout, "7200");
    await user.click(screen.getByRole("button", { name: "切换到 gpt-image-edit" }));
    await waitFor(() => expect(screen.queryByRole("button", { name: "切换到 gpt-image-edit" })).not.toBeInTheDocument());
    await user.click(screen.getByRole("button", { name: "保存" }));

    // 只提交未保存的超时，不把旧图片默认模型写回去
    await waitFor(() => expect(patch).toHaveBeenCalledWith({ video_poll_timeout_seconds: 7200 }));
  });

  it("offers a new recommendation when the default model is empty", async () => {
    const user = userEvent.setup();
    const change = { media_type: "image" as const, provider_id: "openai", recommended: "openai/gpt-image-edit", current: null };
    vi.spyOn(API, "getRecommendationChanges").mockResolvedValue({ changes: [change] });
    const acknowledge = vi.spyOn(API, "acknowledgeRecommendation").mockResolvedValue();

    render(<MediaModelSection />);

    expect(await screen.findByText("openai 推荐 gpt-image-edit 作为默认模型")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "暂不设置" }));
    expect(acknowledge).toHaveBeenCalledWith(change, false);
  });

  it("saves the global video polling timeout", async () => {
    const user = userEvent.setup();
    const patch = vi.spyOn(API, "updateSystemConfig").mockResolvedValue(CONFIG as never);
    render(<MediaModelSection />);

    const timeout = await screen.findByRole("textbox", { name: "视频轮询超时（秒）" });
    await user.clear(timeout);
    await user.type(timeout, "7200");
    await user.click(screen.getByRole("button", { name: "保存" }));

    await waitFor(() => expect(patch).toHaveBeenCalledWith({ video_poll_timeout_seconds: 7200 }));
  });

  it("rounds a fractional polling timeout to an integer on blur before saving", async () => {
    const user = userEvent.setup();
    const patch = vi.spyOn(API, "updateSystemConfig").mockResolvedValue(CONFIG as never);
    render(<MediaModelSection />);

    const timeout = await screen.findByRole("textbox", { name: "视频轮询超时（秒）" });
    await user.clear(timeout);
    await user.type(timeout, "60.5");
    // 编辑期间保留原始字符串，小数点等中间态不被吞掉
    expect(timeout).toHaveValue("60.5");
    await user.tab();
    expect(timeout).toHaveValue("61");
    await user.click(screen.getByRole("button", { name: "保存" }));

    await waitFor(() => expect(patch).toHaveBeenCalledWith({ video_poll_timeout_seconds: 61 }));
  });

  it("discards the polling timeout edit, including typed intermediates, when the field is left empty", async () => {
    const user = userEvent.setup();
    render(<MediaModelSection />);

    const timeout = await screen.findByRole("textbox", { name: "视频轮询超时（秒）" });
    // 先键入有效值（键入过程会把中间值写进未保存修改），再清空离开
    await user.clear(timeout);
    await user.type(timeout, "7200");
    await user.clear(timeout);
    expect(timeout).toHaveValue("");
    await user.tab();

    // 空输入撤销该字段的未保存编辑：回显已保存值，键入过的 7200 不残留在未保存修改里
    expect(timeout).toHaveValue("3600");
    expect(screen.getByRole("button", { name: "保存" })).toBeDisabled();
  });

  it("keeps configured global sub-fields visible when the candidate fetch fails", async () => {
    // 候选接口失败不应让已生效的全局覆盖从界面消失——它在后端仍参与解析，藏起来用户无从察觉
    mockConfig({ default_video_backend_i2v: "ark/seedance" });
    vi.spyOn(API, "getModelCandidates").mockRejectedValue(new Error("boom"));
    render(<MediaModelSection />);
    const i2v = await screen.findByRole("combobox", { name: "图生视频" });
    expect(i2v).toHaveTextContent("seedance");
    // 未配置的细分项没有候选可选，仍不渲染
    expect(screen.queryByRole("combobox", { name: "参考生视频" })).not.toBeInTheDocument();
  });

  it("shows an explicit error notice with a retry entry when the candidate fetch fails, even with no saved overrides", async () => {
    // 全局层未配置过任何细分项时折叠区本会整块消失，失败态须把它留下，用户才拿得到失败信号
    vi.spyOn(API, "getModelCandidates").mockRejectedValue(new Error("boom"));
    render(<MediaModelSection />);
    await screen.findByRole("combobox", { name: "默认视频模型" });
    const alerts = await screen.findAllByRole("alert");
    expect(alerts.length).toBeGreaterThanOrEqual(2); // video + image 两处折叠区
    for (const alert of alerts) {
      expect(alert).toHaveTextContent(/模型列表加载失败/);
    }
    expect(screen.getAllByRole("button", { name: "重试" }).length).toBeGreaterThanOrEqual(2);
  });

  it("recovers normal rendering after a retry succeeds", async () => {
    const user = userEvent.setup();
    const getCandidates = vi
      .spyOn(API, "getModelCandidates")
      .mockRejectedValueOnce(new Error("boom"))
      .mockResolvedValueOnce(CANDIDATES as unknown as Awaited<ReturnType<typeof API.getModelCandidates>>);
    render(<MediaModelSection />);
    await screen.findByRole("combobox", { name: "默认视频模型" });
    const retryButtons = await screen.findAllByRole("button", { name: "重试" });
    await user.click(retryButtons[0]);

    await waitFor(() => expect(getCandidates).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.queryAllByRole("alert")).toHaveLength(0));
    // 失败态强制展开过折叠区，重试成功后仍展开，无需再次点开
    await user.click(screen.getByRole("combobox", { name: "参考生视频" }));
    expect(await screen.findByRole("option", { name: /seedance/ })).toBeInTheDocument();
  });

  it("filters sub-field candidates by purpose while the default dropdown stays unfiltered", async () => {
    const user = userEvent.setup();
    render(<MediaModelSection />);
    const videoDefault = await screen.findByRole("combobox", { name: "默认视频模型" });

    await user.click(videoDefault);
    expect(await screen.findByRole("option", { name: /veo-3/ })).toBeInTheDocument();
    expect(await screen.findByRole("option", { name: /seedance/ })).toBeInTheDocument();
    await user.keyboard("{Escape}");

    await user.click(screen.getAllByText("按用途指定模型")[0]);
    await user.click(screen.getByRole("combobox", { name: "参考生视频" }));
    expect(await screen.findByRole("option", { name: /seedance/ })).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: /veo-3/ })).not.toBeInTheDocument();
  });

  it("names the models a purpose filters out and why, without letting them be picked", async () => {
    const user = userEvent.setup();
    vi.spyOn(API, "getModelCandidates").mockResolvedValue({
      ...CANDIDATES,
      video: { ...CANDIDATES.video, filtered: { r2v: [{ option: "gemini/veo-3", unmet: ["max_reference_images"] }] } },
    } as unknown as Awaited<ReturnType<typeof API.getModelCandidates>>);
    render(<MediaModelSection />);
    await screen.findByRole("combobox", { name: "默认视频模型" });

    await user.click(screen.getAllByText("按用途指定模型")[0]);
    await user.click(screen.getByRole("combobox", { name: "参考生视频" }));
    const veo = await screen.findByRole("option", { name: /veo-3/ });
    expect(veo).toHaveTextContent("不接受参考图");
    expect(veo).toHaveAttribute("aria-disabled", "true");
    expect(screen.getByRole("group", { name: "不能用于参考生视频" })).toContainElement(veo);
  });

  it("shows the global default model behind 跟随默认 in each unset sub-field", async () => {
    const user = userEvent.setup();
    render(<MediaModelSection />);
    await screen.findByRole("combobox", { name: "默认图片模型" });
    await user.click(screen.getAllByText("按用途指定模型")[1]);
    expect(screen.getByRole("combobox", { name: "文生图" })).toHaveTextContent(
      /跟随默认 · gemini · nano-banana/,
    );
  });

  it("shows the auto-selected model when the default model is unset", async () => {
    const user = userEvent.setup();
    mockConfig({ default_video_backend: "", default_image_backend: "" });
    vi.spyOn(API, "getAutoBackends").mockResolvedValue({ ...NO_AUTO, i2v: "ark/seedance", t2i: "gemini/nano-banana" });
    render(<MediaModelSection />);
    expect(await screen.findByRole("combobox", { name: "默认视频模型" })).toHaveTextContent(
      "自动选择（当前：ark · seedance）",
    );
    expect(screen.getByRole("combobox", { name: "默认图片模型" })).toHaveTextContent(
      "自动选择（当前：gemini · nano-banana）",
    );
    // 自动推断按用途过滤：图生图选不出模型时如实说没有可用供应商
    await user.click(screen.getAllByText("按用途指定模型")[1]);
    expect(screen.getByRole("combobox", { name: "图生图" })).toHaveTextContent("自动选择（没有可用的供应商）");
  });

  it("persists a sub-field selection to its own settings key", async () => {
    const user = userEvent.setup();
    const patch = vi
      .spyOn(API, "updateSystemConfig")
      .mockResolvedValue(CONFIG as unknown as Awaited<ReturnType<typeof API.updateSystemConfig>>);
    render(<MediaModelSection />);
    await screen.findByRole("combobox", { name: "默认视频模型" });

    await user.click(screen.getAllByText("按用途指定模型")[0]);
    await user.click(screen.getByRole("combobox", { name: "参考生视频" }));
    await user.click(await screen.findByRole("option", { name: /seedance/ }));
    await user.click(screen.getByRole("button", { name: /保存|Save/ }));

    await waitFor(() =>
      expect(patch).toHaveBeenCalledWith({ default_video_backend_r2v: "ark/seedance" }),
    );
  });

  it("renders the form and completes a save while the candidate request never settles", async () => {
    const user = userEvent.setup();
    vi.spyOn(API, "getModelCandidates").mockReturnValue(
      new Promise(() => {}) as ReturnType<typeof API.getModelCandidates>,
    );
    const patch = vi
      .spyOn(API, "updateSystemConfig")
      .mockResolvedValue(CONFIG as unknown as Awaited<ReturnType<typeof API.updateSystemConfig>>);
    render(<MediaModelSection />);

    await screen.findByRole("combobox", { name: "默认视频模型" });
    await user.click(screen.getByRole("combobox", { name: "默认视频模型" }));
    await user.click(await screen.findByRole("option", { name: /seedance/ }));
    await user.click(screen.getByRole("button", { name: /保存|Save/ }));

    await waitFor(() =>
      expect(patch).toHaveBeenCalledWith({ default_video_backend: "ark/seedance" }),
    );
    // 保存结束的可观测证据：保存栏显示「已保存」——PATCH 已返回但流程仍卡在候选请求上时，
    // 保存栏会一直停在「正在保存」。
    expect(await screen.findByText("已保存")).toBeInTheDocument();
  });

  describe("音频勾选框的模型可控性", () => {
    function videoProvider(
      providerId: string,
      modelId: string,
      audio: { audio_track: VideoAudioControl; reference_route_audio_track?: VideoAudioControl },
    ): ProviderInfo {
      return {
        id: providerId,
        display_name: providerId,
        description: "",
        status: "ready",
        media_types: ["video"],
        capabilities: [],
        credential_count: 0,
        models: {
          [modelId]: {
            display_name: modelId,
            media_type: "video",
            capabilities: [],
            default: true,
            supported_durations: [8],
            resolutions: [],
            reference_route_audio_track: audio.audio_track,
            ...audio,
            voice_consistency: audio.audio_track === "always_off" ? "none" : "soft",
          },
        },
      };
    }

    function mockProviders(audioTrack: VideoAudioControl) {
      vi.spyOn(providerModels, "getProviderModels").mockResolvedValue([
        videoProvider("gemini", "veo-3", { audio_track: audioTrack }),
      ]);
    }

    it("keeps the checkbox interactive for a model whose audio track is controllable", async () => {
      mockProviders("controllable");
      render(<MediaModelSection />);
      const box = await screen.findByRole("checkbox", { name: /生成有声视频/ });
      expect(box).toBeEnabled();
    });

    it("locks the checkbox on an always-audible model and offers a one-click fix for a stored off setting", async () => {
      const user = userEvent.setup();
      mockProviders("always_on");
      render(<MediaModelSection />);
      const box = await screen.findByRole("checkbox", { name: /生成有声视频/ });
      expect(box).toHaveAttribute("aria-disabled", "true");
      expect(box).toBeChecked();
      expect(screen.getByText(/始终带声音/)).toBeInTheDocument();

      const patch = vi
        .spyOn(API, "updateSystemConfig")
        .mockResolvedValue(CONFIG as unknown as Awaited<ReturnType<typeof API.updateSystemConfig>>);
      await user.click(screen.getByRole("button", { name: "改为开启" }));
      await user.click(screen.getByRole("button", { name: /保存|Save/ }));
      await waitFor(() => expect(patch).toHaveBeenCalledWith({ video_generate_audio: true }));
    });

    // 置灰要求两个生效桶同为不可控，提示只要一个桶恒有声就给：两桶判定不一致时置灰会连带
    // 禁掉可控桶的合法关闭。判据漏掉细分桶则会让这份配置一路带到入队才被拒。
    it("warns about a stored off setting when only a capability-bucket override is always audible", async () => {
      const user = userEvent.setup();
      mockConfig({ default_video_backend_i2v: "dashscope/wan" });
      vi.spyOn(providerModels, "getProviderModels").mockResolvedValue([
        videoProvider("gemini", "veo-3", { audio_track: "controllable" }),
        videoProvider("dashscope", "wan", { audio_track: "always_on" }),
      ]);
      render(<MediaModelSection />);
      const box = await screen.findByRole("checkbox", { name: /生成有声视频/ });
      expect(box).toBeEnabled();
      expect(screen.getByRole("alert")).toHaveTextContent(/无法关闭声音/);

      const patch = vi
        .spyOn(API, "updateSystemConfig")
        .mockResolvedValue(CONFIG as unknown as Awaited<ReturnType<typeof API.updateSystemConfig>>);
      await user.click(screen.getByRole("button", { name: "改为开启" }));
      await user.click(screen.getByRole("button", { name: /保存|Save/ }));
      await waitFor(() =>
        expect(patch).toHaveBeenCalledWith(expect.objectContaining({ video_generate_audio: true })),
      );
    });

    it("keeps the checkbox interactive when both bucket overrides are controllable, whatever the base default is", async () => {
      mockConfig({
        default_video_backend: "dashscope/wan",
        default_video_backend_i2v: "gemini/veo-3",
        default_video_backend_r2v: "gemini/veo-3",
      });
      vi.spyOn(providerModels, "getProviderModels").mockResolvedValue([
        videoProvider("gemini", "veo-3", { audio_track: "controllable" }),
        videoProvider("dashscope", "wan", { audio_track: "always_on" }),
      ]);
      render(<MediaModelSection />);
      const box = await screen.findByRole("checkbox", { name: /生成有声视频/ });
      expect(box).toBeEnabled();
      expect(box).not.toBeChecked();
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    });

    // 各层全空时执行期用自动选中的模型，勾选框判据与规格条要按它取，否则恒有声的自动模型
    // 配上「关闭」时一路到入队预检才被拒。
    it("judges the checkbox and shows the spec bar by the auto-selected model when every layer is unset", async () => {
      mockConfig({ default_video_backend: "" });
      vi.spyOn(API, "getAutoBackends").mockResolvedValue({ ...NO_AUTO, i2v: "dashscope/wan", r2v: "gemini/veo-3" });
      vi.spyOn(providerModels, "getProviderModels").mockResolvedValue([
        videoProvider("gemini", "veo-3", { audio_track: "controllable" }),
        videoProvider("dashscope", "wan", { audio_track: "always_on" }),
      ]);
      render(<MediaModelSection />);
      const box = await screen.findByRole("checkbox", { name: /生成有声视频/ });
      expect(box).toBeEnabled();
      expect(screen.getByRole("alert")).toHaveTextContent(/无法关闭声音/);
      expect(screen.getByText("支持时长")).toBeInTheDocument();
    });

    it("locks the checkbox on a model without an audio track", async () => {
      mockProviders("always_off");
      render(<MediaModelSection />);
      const box = await screen.findByRole("checkbox", { name: /生成有声视频/ });
      expect(box).toHaveAttribute("aria-disabled", "true");
      expect(box).not.toBeChecked();
      expect(screen.getByText(/没有声音/)).toBeInTheDocument();
    });

    // 全局页无项目上下文，默认模型两条路径都会用到，能力线只能给目录位；但两个细分项下拉
    // 各自就是一条路径，与上方按桶取值的勾选框判据同源，不能共用默认层那一个答案。
    describe("候选下拉的音轨能力线", () => {
      function mockOmni() {
        vi.spyOn(API, "getSystemConfig").mockResolvedValue({
          options: { ...CONFIG.options, video_backends: ["kling/v3-omni"] },
          settings: { ...CONFIG.settings, default_video_backend: "" },
        } as unknown as Awaited<ReturnType<typeof API.getSystemConfig>>);
        vi.spyOn(API, "getModelCandidates").mockResolvedValue({
          ...CANDIDATES,
          video: {
            default: ["kling/v3-omni"],
            buckets: { i2v: ["kling/v3-omni"], r2v: ["kling/v3-omni"] },
            filtered: {},
          },
        } as unknown as Awaited<ReturnType<typeof API.getModelCandidates>>);
        vi.spyOn(providerModels, "getProviderModels").mockResolvedValue([
          videoProvider("kling", "v3-omni", {
            audio_track: "controllable",
            reference_route_audio_track: "always_off",
          }),
        ]);
      }

      /** 打开指定下拉，读出 v3-omni 那一行的能力线，再关掉——同时只开一个下拉。 */
      async function omniRowIn(user: ReturnType<typeof userEvent.setup>, comboboxName: string) {
        await user.click(screen.getByRole("combobox", { name: comboboxName }));
        const text = (await screen.findByRole("option", { name: /v3-omni/ })).textContent ?? "";
        await user.keyboard("{Escape}");
        return text;
      }

      it("默认模型按目录位标有声，两个细分项各按自己的路径标注", async () => {
        const user = userEvent.setup();
        mockOmni();
        render(<MediaModelSection />);
        await screen.findByRole("combobox", { name: "默认视频模型" });
        expect(await omniRowIn(user, "默认视频模型")).toContain("有声");

        await user.click(screen.getAllByText("按用途指定模型")[0]);
        expect(await omniRowIn(user, "图生视频")).toContain("有声");
        // 与同屏 r2vAudioControl（always_off）判定的勾选框一致：一屏之内两句话不能互相矛盾
        expect(await omniRowIn(user, "参考生视频")).toContain("无声");
      });
    });
  });

  it("lists the default audio model's voices under the narration voice", async () => {
    const user = userEvent.setup();
    vi.spyOn(API, "getSystemConfig").mockResolvedValue({
      options: { ...CONFIG.options, audio_backends: ["dashscope/qwen-audio-3.0-tts-plus"] },
      settings: {
        ...CONFIG.settings,
        default_audio_backend: "dashscope/qwen-audio-3.0-tts-plus",
        narration_voice: "longanlingxin",
      },
    } as unknown as Awaited<ReturnType<typeof API.getSystemConfig>>);
    const capabilities = vi.spyOn(API, "getTtsModelCapabilities").mockResolvedValue({
      supports_speed: true,
      voices: [
        { id: "longanlingxin", label: "龙安灵心 · 知心温暖音（女）" },
        { id: "longanlufeng", label: "龙安鲁风 · 明亮开朗音（男）" },
      ],
    });
    render(<MediaModelSection />);

    const input = await screen.findByLabelText("旁白音色 ID");
    await screen.findByText("从列表选择音色，或照所选供应商文档填写音色 id；留空使用默认音色");
    expect(capabilities).toHaveBeenCalledWith("dashscope/qwen-audio-3.0-tts-plus", expect.anything());
    await user.clear(input);
    await user.click(input);
    await user.click(await screen.findByRole("option", { name: "longanlufeng · 龙安鲁风 · 明亮开朗音（男）" }));
    expect(input).toHaveValue("longanlufeng");
  });

  it("does not ask for voices while the default audio model is auto-selected", async () => {
    const capabilities = vi.spyOn(API, "getTtsModelCapabilities");
    render(<MediaModelSection />);

    await userEvent.click(await screen.findByLabelText("旁白音色 ID"));
    expect(screen.queryByRole("option")).not.toBeInTheDocument();
    expect(capabilities).not.toHaveBeenCalled();
  });

  it("auto-expands a channel whose sub-field is already configured", async () => {
    mockConfig({ default_image_backend_i2i: "openai/gpt-image-edit" });
    render(<MediaModelSection />);
    await screen.findByRole("combobox", { name: "默认图片模型" });
    const imageSection = screen.getAllByRole("button", { name: /按用途指定模型/ })[1];
    expect(imageSection).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("已指定 1 项")).toBeInTheDocument();
  });
});

describe("MediaModelSection – 语言切换", () => {
  // 译名由后端按 Accept-Language 成文，替身按当前语言返回对应译名。
  function candidatesFor(language: string) {
    const en = language.startsWith("en");
    return {
      ...CANDIDATES,
      provider_names: { gemini: en ? "Gemini" : "谷歌" },
      model_names: { "gemini/veo-3": en ? "Veo 3" : "维奥 3" },
    } as unknown as Awaited<ReturnType<typeof API.getModelCandidates>>;
  }

  beforeEach(() => {
    useAppStore.setState(useAppStore.getInitialState(), true);
    vi.restoreAllMocks();
    mockConfig();
    vi.spyOn(API, "getAutoBackends").mockResolvedValue(NO_AUTO);
    vi.spyOn(API, "getModelCandidates").mockImplementation(async () => candidatesFor(i18n.language));
    vi.spyOn(providerModels, "getProviderModels").mockResolvedValue([]);
    vi.spyOn(providerModels, "getCustomProviderModels").mockResolvedValue([]);
  });

  afterEach(async () => {
    await act(async () => {
      await i18n.changeLanguage("zh");
    });
  });

  it("换语言后下拉改用新语言的译名，未保存的编辑仍在", async () => {
    const user = userEvent.setup();
    render(<MediaModelSection />);
    const trigger = await screen.findByRole("combobox", { name: "默认视频模型" });
    await waitFor(() => expect(trigger).toHaveTextContent("谷歌 · 维奥 3"));

    const timeout = screen.getByRole("textbox", { name: "视频轮询超时（秒）" });
    await user.clear(timeout);
    await user.type(timeout, "7200");

    await act(async () => {
      await i18n.changeLanguage("en");
    });

    await waitFor(() => expect(trigger).toHaveTextContent("Gemini · Veo 3"));
    // 语言切换只重取候选，不重取整页配置——后者会 setDraft({}) 丢掉这里未保存的输入
    expect(timeout).toHaveValue("7200");

    await user.click(trigger);
    // 选项行主行为译名，model id 仍在行内可辨识
    expect(await screen.findByRole("option", { name: /Veo 3/ })).toHaveTextContent("veo-3");
  });
});

describe("MediaModelSection – 引用处的下线提示", () => {
  type Candidates = Awaited<ReturnType<typeof API.getModelCandidates>>;

  function mockLifecycle(lifecycle: Candidates["lifecycle"]) {
    vi.spyOn(API, "getModelCandidates").mockResolvedValue({
      ...CANDIDATES,
      model_names: { "gemini/veo-4": "Veo 4" },
      lifecycle,
    } as unknown as Candidates);
  }

  beforeEach(() => {
    useAppStore.setState(useAppStore.getInitialState(), true);
    vi.restoreAllMocks();
    vi.spyOn(API, "getAutoBackends").mockResolvedValue(NO_AUTO);
    vi.spyOn(providerModels, "getProviderModels").mockResolvedValue([]);
    vi.spyOn(providerModels, "getCustomProviderModels").mockResolvedValue([]);
  });

  it("shows the retiring date and successor under every reference, offering a switch only where the successor qualifies", async () => {
    const user = userEvent.setup();
    mockConfig({ default_video_backend_i2v: "gemini/veo-3" });
    mockLifecycle({
      "gemini/veo-3": {
        status: "retiring",
        shutdown_date: "2026-11-24",
        successor: "google/veo-4",
        successor_name: "Veo 4",
        // 后继不支持图生视频：这一处没有切换目标，只写后继名称
        switch_targets: { default: "gemini/veo-4", i2v: null, r2v: null },
      },
    });
    const patch = vi
      .spyOn(API, "updateSystemConfig")
      .mockResolvedValue(CONFIG as unknown as Awaited<ReturnType<typeof API.updateSystemConfig>>);
    render(<MediaModelSection />);

    const notices = await screen.findAllByText("即将下线（2026-11-24），官方建议改用 Veo 4");
    expect(notices).toHaveLength(2);
    // 只有默认层有切换入口
    expect(screen.getAllByRole("button", { name: "切换到 Veo 4" })).toHaveLength(1);

    await user.click(screen.getByRole("button", { name: "切换到 Veo 4" }));
    await user.click(screen.getByRole("button", { name: "保存" }));

    // 一键切换只改这一处：图生视频的引用原样保留
    await waitFor(() => expect(patch).toHaveBeenCalledWith({ default_video_backend: "gemini/veo-4" }));
  });

  it("tells a discontinued reference without a successor to pick another model, with no switch", async () => {
    mockConfig({ default_text_backend: "gemini/g25" });
    mockLifecycle({
      "gemini/g25": {
        status: "discontinued",
        shutdown_date: null,
        successor: null,
        successor_name: null,
        switch_targets: { default: null },
      },
    });
    render(<MediaModelSection />);

    expect(await screen.findByText("已下线，无法继续生成，请重新选择模型")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /切换到/ })).not.toBeInTheDocument();
  });

  it("offers the successor switch for a discontinued reference that has left the provider list", async () => {
    const user = userEvent.setup();
    // 官方数据删除了清单项、只剩本地保留条目的旧型号：值不在任何候选里，提示照样给出
    mockConfig({ default_video_backend: "gemini/veo-3-preview" });
    mockLifecycle({
      "gemini/veo-3-preview": {
        status: "discontinued",
        shutdown_date: "2026-10-22",
        successor: "google/veo-4",
        successor_name: "Veo 4",
        switch_targets: { default: "gemini/veo-4", i2v: null, r2v: null },
      },
    });
    const patch = vi
      .spyOn(API, "updateSystemConfig")
      .mockResolvedValue(CONFIG as unknown as Awaited<ReturnType<typeof API.updateSystemConfig>>);
    render(<MediaModelSection />);

    expect(
      await screen.findByText("已下线（2026-10-22），无法继续生成，官方建议改用 Veo 4"),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "切换到 Veo 4" }));
    await user.click(screen.getByRole("button", { name: "保存" }));

    await waitFor(() => expect(patch).toHaveBeenCalledWith({ default_video_backend: "gemini/veo-4" }));
  });

  it("asks to pick another model when a reference has no lifecycle to show", async () => {
    mockConfig({ default_image_backend: "gemini/nano-banana-preview" });
    mockLifecycle({
      "gemini/nano-banana-preview": {
        status: "unavailable",
        shutdown_date: null,
        successor: null,
        successor_name: null,
        switch_targets: { default: null, t2i: null, i2i: null },
      },
    });
    render(<MediaModelSection />);

    expect(await screen.findByText("已不可用，请重新选择模型")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /切换到/ })).not.toBeInTheDocument();
  });

  it("names the successor of a discontinued reference when it cannot be switched to here", async () => {
    mockConfig();
    mockLifecycle({
      "gemini/nano-banana": {
        status: "discontinued",
        shutdown_date: "2026-10-22",
        successor: "google/nano-banana-2",
        successor_name: "Nano Banana 2",
        switch_targets: { default: null, t2i: null, i2i: null },
      },
    });
    render(<MediaModelSection />);

    expect(
      await screen.findByText("已下线（2026-10-22），无法继续生成，官方建议改用 Nano Banana 2"),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /切换到/ })).not.toBeInTheDocument();
  });
});
