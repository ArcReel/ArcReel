import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import "@/i18n";
import { API } from "@/api";
import * as providerModels from "@/utils/provider-models";
import { useAppStore } from "@/stores/app-store";
import { MediaModelSection } from "./MediaModelSection";

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
    // 候选失败但全局层未配置过任何细分项时，旧行为是整块折叠区消失——用户拿不到失败信号
    vi.spyOn(API, "getModelCandidates").mockRejectedValue(new Error("boom"));
    render(<MediaModelSection />);
    await screen.findByRole("combobox", { name: "默认视频模型" });
    const alerts = await screen.findAllByRole("alert");
    expect(alerts.length).toBeGreaterThanOrEqual(2); // video + image 两处折叠区
    for (const alert of alerts) {
      expect(alert).toHaveTextContent(/候选/);
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

  it("auto-expands a channel whose sub-field is already configured", async () => {
    mockConfig({ default_image_backend_i2i: "openai/gpt-image-edit" });
    const { container } = render(<MediaModelSection />);
    await screen.findByRole("combobox", { name: "默认图片模型" });
    const imageSection = container.querySelectorAll("details")[1];
    expect(imageSection.open).toBe(true);
    expect(screen.getByText("已指定 1 项")).toBeInTheDocument();
  });
});
