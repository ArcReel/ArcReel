import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import "@/i18n";
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
  },
};

const CANDIDATES = {
  image: {
    default: ["gemini/nano-banana", "openai/gpt-image-edit"],
    buckets: { t2i: ["gemini/nano-banana"], i2i: ["gemini/nano-banana", "openai/gpt-image-edit"] },
  },
  video: {
    default: ["gemini/veo-3", "ark/seedance"],
    buckets: { i2v: ["gemini/veo-3"], r2v: ["ark/seedance"] },
  },
  provider_names: {},
};

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
    const sections = Array.from(container.querySelectorAll("details"));
    expect(sections).toHaveLength(3);
    expect(sections.every((d) => !d.open)).toBe(true);
    // 界面文案不出现内部术语
    expect(container.textContent).not.toMatch(/能力桶|capability bucket/i);
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
    expect(screen.getByRole("option", { name: /seedance/ })).toBeInTheDocument();
  });

  it("filters sub-field candidates by purpose while the default dropdown stays unfiltered", async () => {
    const user = userEvent.setup();
    render(<MediaModelSection />);
    const videoDefault = await screen.findByRole("combobox", { name: "默认视频模型" });

    await user.click(videoDefault);
    expect(screen.getByRole("option", { name: /veo-3/ })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: /seedance/ })).toBeInTheDocument();
    await user.keyboard("{Escape}");

    await user.click(screen.getAllByText("按用途指定模型")[0]);
    await user.click(screen.getByRole("combobox", { name: "参考生视频" }));
    expect(screen.getByRole("option", { name: /seedance/ })).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: /veo-3/ })).not.toBeInTheDocument();
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

  it("persists a sub-field selection to its own settings key", async () => {
    const user = userEvent.setup();
    const patch = vi
      .spyOn(API, "updateSystemConfig")
      .mockResolvedValue(CONFIG as unknown as Awaited<ReturnType<typeof API.updateSystemConfig>>);
    render(<MediaModelSection />);
    await screen.findByRole("combobox", { name: "默认视频模型" });

    await user.click(screen.getAllByText("按用途指定模型")[0]);
    await user.click(screen.getByRole("combobox", { name: "参考生视频" }));
    await user.click(screen.getByRole("option", { name: /seedance/ }));
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
    await user.click(screen.getByRole("option", { name: /seedance/ }));
    await user.click(screen.getByRole("button", { name: /保存|Save/ }));

    await waitFor(() =>
      expect(patch).toHaveBeenCalledWith({ default_video_backend: "ark/seedance" }),
    );
    // 保存结束的可观测证据：成功 toast 已推出——PATCH 已返回但流程仍卡在候选请求上时，
    // finally 里的 setSaving(false) 与这条 toast 都不会发生。
    await waitFor(() =>
      expect(useAppStore.getState().toast?.text).toBe("媒体模型配置已保存"),
    );
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
        configured_keys: [],
        missing_keys: [],
        models: {
          [modelId]: {
            display_name: modelId,
            media_type: "video",
            capabilities: [],
            default: true,
            supported_durations: [8],
            duration_resolution_constraints: {},
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
      const box = await screen.findByRole("checkbox", { name: /生成音频/ });
      expect(box).toBeEnabled();
    });

    it("locks the checkbox on an always-audible model and offers a one-click fix for a stored off setting", async () => {
      const user = userEvent.setup();
      mockProviders("always_on");
      render(<MediaModelSection />);
      const box = await screen.findByRole("checkbox", { name: /生成音频/ });
      expect(box).toBeDisabled();
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
      const box = await screen.findByRole("checkbox", { name: /生成音频/ });
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
      const box = await screen.findByRole("checkbox", { name: /生成音频/ });
      expect(box).toBeEnabled();
      expect(box).not.toBeChecked();
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    });

    it("locks the checkbox on a model without an audio track", async () => {
      mockProviders("always_off");
      render(<MediaModelSection />);
      const box = await screen.findByRole("checkbox", { name: /生成音频/ });
      expect(box).toBeDisabled();
      expect(box).not.toBeChecked();
      expect(screen.getByText(/没有声音/)).toBeInTheDocument();
    });
  });

  it("auto-expands a channel whose sub-field is already configured", async () => {
    mockConfig({ default_image_backend_i2i: "openai/gpt-image-edit" });
    const { container } = render(<MediaModelSection />);
    await screen.findByRole("combobox", { name: "默认图片模型" });
    const imageSection = container.querySelectorAll("details")[1];
    expect(imageSection.open).toBe(true);
    expect(screen.getByText("已指定 1 项")).toBeInTheDocument();
  });
});
