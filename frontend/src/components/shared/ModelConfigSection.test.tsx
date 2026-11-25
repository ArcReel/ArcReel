import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, it, expect, vi } from "vitest";
import type { ComponentProps } from "react";
import userEvent from "@testing-library/user-event";
import { API, ApiRequestError, type VideoCapabilitiesQuery } from "@/api";
import { ModelConfigSection } from "./ModelConfigSection";
import { useEndpointCatalogStore } from "@/stores/endpoint-catalog-store";
import type {
  CustomProviderInfo,
  DurationConstraints,
  EndpointDescriptor,
  ModelInfoResponse,
  ProviderInfo,
  VideoCapabilities,
} from "@/types";
import { lookupSupportedDurations } from "@/utils/provider-models";

const PROVIDERS: ProviderInfo[] = [
  {
    id: "gemini",
    display_name: "Gemini",
    description: "",
    status: "ready",
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
        resolutions: [],
        qualities: [],
        audio_track: "controllable",
        reference_route_audio_track: "controllable",
        voice_consistency: "soft",
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
    credential_count: 0,
    models: {
      seedance: {
        display_name: "seedance",
        media_type: "video",
        capabilities: [],
        default: false,
        supported_durations: [5, 8, 10],
        resolutions: [],
        qualities: [],
        audio_track: "controllable",
        reference_route_audio_track: "controllable",
        voice_consistency: "soft",
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
  videoProviderI2V: "",
  videoProviderR2V: "",
  imageBackendDefault: "",
  imageBackendT2I: "",
  imageBackendI2I: "",
  textBackendDefault: "",
  textBackendSimple: "",
  textBackendComplex: "",
  defaultDuration: null,
  videoResolution: null,
  imageResolution: null,
  imageQuality: null,
} as const;

const EMPTY_GLOBALS = {
  video: "", videoI2V: "", videoR2V: "",
  image: "", imageT2I: "", imageI2I: "",
  textDefault: "", textSimple: "", textComplex: "",
} as const;

let fakeCatalog: ProviderInfo[] = PROVIDERS;

function veoConstraints(query: VideoCapabilitiesQuery): DurationConstraints {
  const resolution = query.resolution ?? null;
  const usesReferenceImages = query.usesReferenceImages ?? false;
  const base = { resolution, uses_reference_images: usesReferenceImages };
  if (usesReferenceImages) {
    return {
      ...base,
      allowed: [8],
      excluded: { "4": "reference", "6": "reference" },
    };
  }
  if (resolution === "1080p" || resolution === "4k") {
    return { ...base, allowed: [8], excluded: { "4": "resolution", "6": "resolution" } };
  }
  return { ...base, allowed: [4, 6, 8], excluded: {} };
}

function fakeVideoCapabilities(videoBackend: string, query: VideoCapabilitiesQuery): Promise<VideoCapabilities> {
  const durations = lookupSupportedDurations(fakeCatalog, videoBackend);
  if (!durations) return Promise.reject(new Error(`unknown model ${videoBackend}`));
  const [provider_id, model] = videoBackend.split("/");
  const sorted = [...durations].sort((a, b) => a - b);
  const duration_constraints: DurationConstraints =
    videoBackend === "gemini-aistudio/veo"
      ? veoConstraints(query)
      : {
          resolution: query.resolution ?? null,
          uses_reference_images: query.usesReferenceImages ?? false,
          allowed: sorted,
          excluded: {},
        };
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
    duration_constraints,
  });
}

beforeEach(() => {
  fakeCatalog = PROVIDERS;
  vi.spyOn(API, "getModelVideoCapabilities").mockImplementation((backend, query = {}) =>
    fakeVideoCapabilities(backend, query),
  );
  vi.spyOn(API, "getVideoCapabilities").mockImplementation((_name, query = {}) =>
    fakeVideoCapabilities(query.videoBackend ?? "", query),
  );
});

describe("ModelConfigSection", () => {
  it("shows the candidate bucket failure and repair hint in the creation form", async () => {
    vi.spyOn(API, "getModelVideoCapabilities").mockRejectedValue(
      new ApiRequestError("请重新选择支持参考生视频的模型", undefined, 400),
    );
    render(<ModelConfigSection showSubFields={false} value={{ ...EMPTY_VALUE, videoBackend: "gemini/veo-3" }}
      onChange={() => {}} providers={PROVIDERS} options={OPTIONS} globalDefaults={EMPTY_GLOBALS}
      usesReferenceImages />);
    expect(await screen.findByRole("alert")).toHaveTextContent("请重新选择支持参考生视频的模型");
  });
  it("shows both reference-video bucket resolutions and keeps a shared model in sync", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    const providers = PROVIDERS.map((provider) => ({
      ...provider,
      models: Object.fromEntries(Object.entries(provider.models).map(([id, model]) => [
        id, { ...model, resolutions: ["720p", "1080p"] },
      ])),
    }));
    const value = {
      ...EMPTY_VALUE,
      videoBackend: "gemini/veo-3",
      videoResolutions: { "gemini/veo-3": "720p" },
      videoResolution: "720p",
    };
    const { rerender } = render(
      <ModelConfigSection value={value} onChange={onChange} providers={providers} options={OPTIONS}
        globalDefaults={EMPTY_GLOBALS} usesReferenceImages />,
    );
    const i2v = await screen.findByRole("combobox", { name: /图生视频.*分辨率|Image to video.*Resolution/ });
    const r2v = screen.getByRole("combobox", { name: /参考生视频.*分辨率|Reference to video.*Resolution/ });
    expect(i2v).toHaveTextContent("720p");
    expect(r2v).toHaveTextContent("720p");
    await user.click(i2v);
    await user.click(await screen.findByRole("option", { name: "1080p" }));
    const next = onChange.mock.lastCall?.[0];
    rerender(<ModelConfigSection value={{ ...next, videoResolution: next.videoResolutions["gemini/veo-3"] }}
      onChange={onChange} providers={providers} options={OPTIONS} globalDefaults={EMPTY_GLOBALS} usesReferenceImages />);
    expect(r2v).toHaveTextContent("1080p");
  });
  it("renders only the three default-layer selectors when no candidates are supplied", async () => {
    const user = userEvent.setup();
    render(
      <ModelConfigSection
        showSubFields={false}
        value={EMPTY_VALUE}
        onChange={() => {}}
        providers={PROVIDERS}
        options={OPTIONS}
        globalDefaults={{
          ...EMPTY_GLOBALS,
          video: "gemini/veo-3",
          image: "gemini/nano-banana",
          textDefault: "gemini/g25",
          textSimple: "gemini/g25",
          textComplex: "gemini/g25",
        }}
      />,
    );
    const comboboxes = screen.getAllByRole("combobox");
    expect(comboboxes).toHaveLength(3);
    expect(screen.queryByText("按用途指定模型")).not.toBeInTheDocument();

    await user.click(comboboxes[0]);
    expect(await screen.findByRole("option", { name: /使用全局默认/ })).toBeInTheDocument();
    await user.click(comboboxes[0]);
  });

  it("keeps text tiers when media candidates are unavailable", async () => {
    render(
      <ModelConfigSection
        candidates={null}
        value={EMPTY_VALUE}
        onChange={() => {}}
        providers={PROVIDERS}
        options={OPTIONS}
        globalDefaults={{ ...EMPTY_GLOBALS, textDefault: "gemini/g25" }}
      />,
    );
    const disclosures = screen.getAllByRole("button", { name: /按用途指定模型/ });
    expect(disclosures).toHaveLength(1);
    await userEvent.setup().click(disclosures[0]);
    expect(screen.getByRole("combobox", { name: "简单任务" })).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "复杂任务" })).toBeInTheDocument();
  });

  it("keeps configured sub-fields visible and clearable when candidates are unavailable", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      <ModelConfigSection
        candidates={null}
        value={{ ...EMPTY_VALUE, imageBackendT2I: "gemini/nano-banana" }}
        onChange={onChange}
        providers={PROVIDERS}
        options={OPTIONS}
        globalDefaults={EMPTY_GLOBALS}
      />,
    );

    const t2i = screen.getByRole("combobox", { name: "文生图" });
    expect(t2i).toHaveTextContent("nano-banana");
    expect(screen.queryByRole("combobox", { name: "图生图" })).not.toBeInTheDocument();

    await user.click(t2i);
    await user.click(await screen.findByRole("option", { name: /跟随默认/ }));
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ imageBackendT2I: "" }));
  });

  it("shows an explicit error notice with a retry entry when candidatesError is set, even with no saved overrides", async () => {
    const user = userEvent.setup();
    const onRetry = vi.fn();
    render(
      <ModelConfigSection
        candidates={null}
        candidatesError={{ onRetry }}
        value={EMPTY_VALUE}
        onChange={() => {}}
        providers={PROVIDERS}
        options={OPTIONS}
        globalDefaults={EMPTY_GLOBALS}
      />,
    );
    const alerts = screen.getAllByRole("alert");
    expect(alerts).toHaveLength(2);
    for (const alert of alerts) {
      expect(alert).toHaveTextContent(/模型列表加载失败/);
    }
    const retryButtons = screen.getAllByRole("button", { name: "重试" });
    expect(retryButtons).toHaveLength(2);
    await user.click(retryButtons[0]);
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("does not show the error notice when candidates is merely absent without candidatesError", () => {
    render(
      <ModelConfigSection
        candidates={null}
        value={EMPTY_VALUE}
        onChange={() => {}}
        providers={PROVIDERS}
        options={OPTIONS}
        globalDefaults={EMPTY_GLOBALS}
      />,
    );
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  describe("按用途指定模型（项目层）", () => {
    const CANDIDATES = {
      image: {
        default: ["gemini/nano-banana", "openai/gpt-image-edit"],
        buckets: {
          t2i: ["gemini/nano-banana"],
          i2i: ["gemini/nano-banana", "openai/gpt-image-edit"],
        },
        filtered: {},
      },
      video: {
        default: ["gemini/veo-3", "ark/seedance"],
        buckets: { i2v: ["gemini/veo-3"], r2v: ["ark/seedance"] },
        filtered: {},
      },
      provider_names: {},
      model_names: {},
      lifecycle: {},
    };

    function renderWithCandidates(
      overrides: Partial<ComponentProps<typeof ModelConfigSection>> = {},
    ) {
      return render(
        <ModelConfigSection
          value={EMPTY_VALUE}
          onChange={() => {}}
          providers={PROVIDERS}
          options={OPTIONS}
          candidates={CANDIDATES}
          globalDefaults={EMPTY_GLOBALS}
          {...overrides}
        />,
      );
    }

    it("collapses the sub-fields by default and names each row after its generation path", async () => {
      const user = userEvent.setup();
      renderWithCandidates();
      const sections = screen.getAllByRole("button", { name: /按用途指定模型/ });
      expect(sections).toHaveLength(3);
      expect(sections.every((d) => d.getAttribute("aria-expanded") === "false")).toBe(true);

      for (const summary of screen.getAllByText("按用途指定模型")) {
        await user.click(summary);
      }
      for (const name of ["图生视频", "参考生视频", "文生图", "图生图", "简单任务", "复杂任务"]) {
        expect(screen.getByRole("combobox", { name })).toBeInTheDocument();
      }
      expect(document.body).not.toHaveTextContent(/能力桶|任务类型桶|capability bucket/i);
    });

    it("feeds each sub-field from its own filtered candidate list while the default layer stays unfiltered", async () => {
      const user = userEvent.setup();
      renderWithCandidates();
      await user.click(screen.getAllByText("按用途指定模型")[0]);

      await user.click(screen.getByRole("combobox", { name: "默认视频模型" }));
      expect(await screen.findByRole("option", { name: /veo-3/ })).toBeInTheDocument();
      expect(await screen.findByRole("option", { name: /seedance/ })).toBeInTheDocument();
      await user.keyboard("{Escape}");

      await user.click(screen.getByRole("combobox", { name: "图生视频" }));
      expect(await screen.findByRole("option", { name: /veo-3/ })).toBeInTheDocument();
      expect(screen.queryByRole("option", { name: /seedance/ })).not.toBeInTheDocument();
    });

    it("switches only the bucket reference whose model is retiring, to the successor that qualifies for that bucket", async () => {
      const user = userEvent.setup();
      const onChange = vi.fn();
      renderWithCandidates({
        value: { ...EMPTY_VALUE, videoBackend: "ark/seedance", videoProviderR2V: "ark/seedance" },
        onChange,
        candidates: {
          ...CANDIDATES,
          lifecycle: {
            "ark/seedance": {
              status: "retiring",
              shutdown_date: "2026-11-24",
              successor: "bytedance/seedance-2",
              successor_name: "Seedance 2",
              switch_targets: { default: null, i2v: null, r2v: "ark/seedance-2" },
            },
          },
        },
      });

      expect(screen.getAllByText(/即将下线（2026-11-24），推荐替代模型为/)).toHaveLength(2);
      await user.click(screen.getByRole("button", { name: "切换到 seedance-2" }));

      expect(onChange).toHaveBeenLastCalledWith(
        expect.objectContaining({ videoBackend: "ark/seedance", videoProviderR2V: "ark/seedance-2" }),
      );
    });

    describe("所选视频模型已下线", () => {
      const DISCONTINUED_ERROR = "Ark/seedance：该模型已于 2026-11-01 下线，推荐替代模型为 Seedance 2；请在设置中重新选择模型";
      const discontinued = (status: "discontinued" | "retiring" = "discontinued") => ({
        ...CANDIDATES,
        lifecycle: {
          "ark/seedance": {
            status,
            shutdown_date: "2026-11-01",
            successor: "bytedance/seedance-2",
            successor_name: "Seedance 2",
            switch_targets: { default: null, i2v: null, r2v: null },
          },
        },
      });

      beforeEach(() => {
        vi.spyOn(API, "getVideoCapabilities").mockRejectedValue(new ApiRequestError(DISCONTINUED_ERROR, undefined, 400));
      });

      it.each([
        ["the project default", { videoBackend: "ark/seedance" }],
        ["a generation-path override", { videoBackend: "gemini/veo-3", videoProviderI2V: "ark/seedance" }],
      ])("shows only the discontinued notice when %s references it", async (_where, value) => {
        renderWithCandidates({ projectName: "demo", value: { ...EMPTY_VALUE, ...value }, candidates: discontinued() });

        expect(await screen.findByText("已下线（2026-11-01），无法继续生成，推荐替代模型为 Seedance 2")).toBeInTheDocument();
        await waitFor(() => expect(API.getVideoCapabilities).toHaveBeenCalled());
        await waitFor(() => expect(screen.getAllByRole("alert")).toHaveLength(1));
        expect(screen.queryByText(DISCONTINUED_ERROR)).not.toBeInTheDocument();
      });

      it("keeps the capability error when the model is executed without a notice here", async () => {
        renderWithCandidates({
          projectName: "demo",
          globalDefaults: { ...EMPTY_GLOBALS, video: "ark/seedance" },
          candidates: discontinued(),
        });

        expect(await screen.findByText(DISCONTINUED_ERROR)).toBeInTheDocument();
      });

      it("keeps the capability error when only another generation path references the model", async () => {
        renderWithCandidates({
          projectName: "demo",
          value: { ...EMPTY_VALUE, videoProviderR2V: "ark/seedance" },
          globalDefaults: { ...EMPTY_GLOBALS, video: "ark/seedance" },
          candidates: discontinued(),
        });

        expect(await screen.findByText(DISCONTINUED_ERROR)).toBeInTheDocument();
      });

      it("keeps the capability error next to a notice that only announces the shutdown", async () => {
        renderWithCandidates({
          projectName: "demo",
          value: { ...EMPTY_VALUE, videoBackend: "ark/seedance" },
          candidates: discontinued("retiring"),
        });

        expect(await screen.findByText(DISCONTINUED_ERROR)).toBeInTheDocument();
        expect(screen.getByText(/即将下线（2026-11-01）/)).toBeInTheDocument();
      });
    });

    it("lets the project default model win over every global layer in the placeholder", async () => {
      const user = userEvent.setup();
      renderWithCandidates({
        value: { ...EMPTY_VALUE, videoBackend: "gemini/veo-3" },
        globalDefaults: { ...EMPTY_GLOBALS, video: "ark/seedance", videoR2V: "ark/seedance" },
      });
      await user.click(screen.getAllByText("按用途指定模型")[0]);
      expect(screen.getByRole("combobox", { name: "图生视频" })).toHaveTextContent(
        /跟随默认 · Gemini · veo-3/,
      );
    });

    it("falls through to the global bucket, then the global default, when the project layer is empty", async () => {
      const user = userEvent.setup();
      renderWithCandidates({
        globalDefaults: { ...EMPTY_GLOBALS, video: "gemini/veo-3", videoR2V: "ark/seedance" },
      });
      await user.click(screen.getAllByText("按用途指定模型")[0]);
      expect(screen.getByRole("combobox", { name: "参考生视频" })).toHaveTextContent(
        /跟随默认 · Ark · seedance/,
      );
      expect(screen.getByRole("combobox", { name: "图生视频" })).toHaveTextContent(
        /跟随默认 · Gemini · veo-3/,
      );
    });

    it("revalidates duration and resolution when a sub-field switches the executing model", async () => {
      const user = userEvent.setup();
      const onChange = vi.fn();
      renderWithCandidates({
        value: {
          ...EMPTY_VALUE,
          videoBackend: "gemini/veo-3",
          defaultDuration: 4,
          videoResolution: "1080p",
        },
        onChange,
        candidates: {
          ...CANDIDATES,
          video: { ...CANDIDATES.video, buckets: { i2v: ["gemini/veo-3", "ark/seedance"], r2v: ["ark/seedance"] } },
        },
      });
      await user.click(screen.getAllByText("按用途指定模型")[0]);
      await user.click(screen.getByRole("combobox", { name: "参考生视频" }));
      await user.click(await screen.findByRole("option", { name: /seedance/ }));
      expect(onChange).toHaveBeenCalledWith(
        expect.objectContaining({
          videoProviderR2V: "ark/seedance",
          defaultDuration: 4,
          videoResolution: "1080p",
        }),
      );

      onChange.mockClear();
      await user.click(screen.getByRole("combobox", { name: "图生视频" }));
      await user.click(await screen.findByRole("option", { name: /seedance/ }));
      expect(onChange).toHaveBeenCalledWith(
        expect.objectContaining({
          videoProviderI2V: "ark/seedance",
          defaultDuration: null,
          videoResolution: null,
        }),
      );
    });

    it("clears the resolution and quality when a sub-field change moves the executing image model", async () => {
      const user = userEvent.setup();
      const onChange = vi.fn();
      renderWithCandidates({
        value: {
          ...EMPTY_VALUE,
          imageBackendDefault: "openai/gpt-image-edit",
          imageResolution: "1080p",
          imageQuality: "high",
        },
        onChange,
      });
      await user.click(screen.getAllByText("按用途指定模型")[1]);
      await user.click(screen.getByRole("combobox", { name: "文生图" }));
      await user.click(await screen.findByRole("option", { name: /nano-banana/ }));
      expect(onChange).toHaveBeenCalledWith(
        expect.objectContaining({ imageBackendT2I: "gemini/nano-banana", imageResolution: null, imageQuality: null }),
      );
    });

    it("auto-expands and counts sub-fields that already carry a value", () => {
      renderWithCandidates({
        value: { ...EMPTY_VALUE, videoProviderR2V: "ark/seedance" },
      });
      const [videoDisclosure] = screen.getAllByRole("button", { name: /按用途指定模型/ });
      expect(videoDisclosure).toHaveAttribute("aria-expanded", "true");
      expect(screen.getByText("已指定 1 项")).toBeInTheDocument();
    });
  });

  it("renders duration buttons based on supported_durations of current video backend", async () => {
    const { rerender } = render(
      <ModelConfigSection
        value={{ ...EMPTY_VALUE, videoBackend: "gemini/veo-3" }}
        onChange={() => {}}
        providers={PROVIDERS}
        options={OPTIONS}
        globalDefaults={EMPTY_GLOBALS}
      />,
    );
    expect(await screen.findByRole("radio", { name: "4 秒" })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "6 秒" })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "8 秒" })).toBeInTheDocument();
    expect(screen.queryByRole("radio", { name: "5 秒" })).not.toBeInTheDocument();

    rerender(
      <ModelConfigSection
        value={{ ...EMPTY_VALUE, videoBackend: "ark/seedance" }}
        onChange={() => {}}
        providers={PROVIDERS}
        options={OPTIONS}
        globalDefaults={EMPTY_GLOBALS}
      />,
    );
    expect(await screen.findByRole("radio", { name: "5 秒" })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "8 秒" })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "10 秒" })).toBeInTheDocument();
    expect(screen.queryByRole("radio", { name: "4 秒" })).not.toBeInTheDocument();
  });

  it("derives duration options from the bucket that the project actually executes", async () => {
    const { rerender } = render(
      <ModelConfigSection
        value={{ ...EMPTY_VALUE, videoBackend: "gemini/veo-3", videoProviderI2V: "ark/seedance" }}
        onChange={() => {}}
        providers={PROVIDERS}
        options={OPTIONS}
        globalDefaults={EMPTY_GLOBALS}
      />,
    );
    expect(await screen.findByRole("radio", { name: "10 秒" })).toBeInTheDocument();
    expect(screen.queryByRole("radio", { name: "4 秒" })).not.toBeInTheDocument();

    rerender(
      <ModelConfigSection
        usesReferenceImages
        value={{ ...EMPTY_VALUE, videoBackend: "gemini/veo-3", videoProviderI2V: "ark/seedance" }}
        onChange={() => {}}
        providers={PROVIDERS}
        options={OPTIONS}
        globalDefaults={EMPTY_GLOBALS}
      />,
    );
    expect(await screen.findByRole("radio", { name: "4 秒" })).toBeInTheDocument();
    expect(screen.queryByRole("radio", { name: "10 秒" })).not.toBeInTheDocument();
  });

  it("keeps duration and resolution when the default layer changes but the bucket still wins", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      <ModelConfigSection
        value={{
          ...EMPTY_VALUE,
          videoBackend: "gemini/veo-3",
          videoProviderI2V: "ark/seedance",
          defaultDuration: 10,
          videoResolution: "1080p",
        }}
        onChange={onChange}
        providers={PROVIDERS}
        options={OPTIONS}
        globalDefaults={EMPTY_GLOBALS}
      />,
    );
    await user.click(screen.getByRole("combobox", { name: "默认视频模型" }));
    await user.click(await screen.findByRole("option", { name: /seedance/ }));
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({ videoBackend: "ark/seedance", defaultDuration: 10, videoResolution: "1080p" }),
    );
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
        globalDefaults={EMPTY_GLOBALS}
      />,
    );
    const videoTrigger = screen.getByRole("combobox", { name: /视频模型/ });
    await user.click(videoTrigger);
    const seedanceOption = await screen.findByRole("option", { name: /seedance/ });
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
        globalDefaults={EMPTY_GLOBALS}
      />,
    );
    const videoTrigger = screen.getByRole("combobox", { name: /视频模型/ });
    await user.click(videoTrigger);
    const seedanceOption = await screen.findByRole("option", { name: /seedance/ });
    await user.click(seedanceOption);

    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({
        videoBackend: "ark/seedance",
        defaultDuration: 8,
      }),
    );
  });

  it("renders the spec bar with a catalog-derived voice consistency tier when no project context is given", () => {
    render(
      <ModelConfigSection
        value={{ ...EMPTY_VALUE, videoBackend: "gemini/veo-3" }}
        onChange={() => {}}
        providers={PROVIDERS}
        options={OPTIONS}
        globalDefaults={EMPTY_GLOBALS}
      />,
    );
    expect(screen.getByText("有声")).toBeInTheDocument();
    expect(screen.getByText("软约束")).toBeInTheDocument();
  });

  it("shows a duration/audio capability line under each option in the video dropdown", async () => {
    const user = userEvent.setup();
    render(
      <ModelConfigSection
        value={EMPTY_VALUE}
        onChange={() => {}}
        providers={PROVIDERS}
        options={OPTIONS}
        globalDefaults={EMPTY_GLOBALS}
      />,
    );
    const videoTrigger = screen.getByRole("combobox", { name: /视频模型/ });
    await user.click(videoTrigger);
    expect(await screen.findByText("5, 8, 10s · 有声")).toBeInTheDocument();
  });

  it("respects enable.video=false to hide the video card", () => {
    render(
      <ModelConfigSection
        value={EMPTY_VALUE}
        onChange={() => {}}
        providers={PROVIDERS}
        options={OPTIONS}
        globalDefaults={EMPTY_GLOBALS}
        enable={{ video: false }}
      />,
    );
    expect(screen.queryByRole("combobox", { name: /视频模型/ })).not.toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: /^默认图片模型$/ })).toBeInTheDocument();
  });

  it("falls back to globalDefaults.video supported_durations when videoBackend is empty (bug repro)", async () => {
    render(
      <ModelConfigSection
        value={EMPTY_VALUE}
        onChange={() => {}}
        providers={PROVIDERS}
        options={OPTIONS}
        globalDefaults={{ ...EMPTY_GLOBALS, video: "ark/seedance" }}
      />,
    );
    expect(await screen.findByRole("radio", { name: "5 秒" })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "8 秒" })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "10 秒" })).toBeInTheDocument();
    expect(screen.queryByRole("radio", { name: "4 秒" })).not.toBeInTheDocument();
    expect(screen.queryByRole("radio", { name: "6 秒" })).not.toBeInTheDocument();
  });

  it("hides duration picker when videoBackend is empty and no global default", () => {
    render(
      <ModelConfigSection
        value={EMPTY_VALUE}
        onChange={() => {}}
        providers={PROVIDERS}
        options={OPTIONS}
        globalDefaults={EMPTY_GLOBALS}
      />,
    );
    expect(screen.queryByRole("radio", { name: "4 秒" })).not.toBeInTheDocument();
    expect(screen.queryByRole("radio", { name: "6 秒" })).not.toBeInTheDocument();
    expect(screen.queryByRole("radio", { name: "8 秒" })).not.toBeInTheDocument();
    expect(API.getModelVideoCapabilities).not.toHaveBeenCalled();
  });

  describe("各层全空时的自动选择", () => {
    const AUTO = { t2i: "gemini/veo-3", i2i: "gemini/veo-3", i2v: "ark/seedance", r2v: "gemini/veo-3", text: "gemini/veo-3" };

    it("shows the auto-selected model and asks its capabilities for durations", async () => {
      render(
        <ModelConfigSection
          value={EMPTY_VALUE}
          onChange={() => {}}
          providers={PROVIDERS}
          options={OPTIONS}
          globalDefaults={{ ...EMPTY_GLOBALS, auto: AUTO }}
        />,
      );
      expect(screen.getByRole("combobox", { name: "默认视频模型" })).toHaveTextContent("自动选择（当前：Ark · seedance）");
      expect(screen.getByRole("combobox", { name: "默认模型" })).toHaveTextContent("自动选择（当前：Gemini · veo-3）");
      expect(await screen.findByRole("radio", { name: "5 秒" })).toBeInTheDocument();
      expect(screen.getByRole("radio", { name: "10 秒" })).toBeInTheDocument();
      expect(screen.queryByRole("radio", { name: "4 秒" })).not.toBeInTheDocument();
    });

    it("follows the bucket of the reference-video path", async () => {
      render(
        <ModelConfigSection
          value={EMPTY_VALUE}
          onChange={() => {}}
          providers={PROVIDERS}
          options={OPTIONS}
          globalDefaults={{ ...EMPTY_GLOBALS, auto: { ...AUTO, i2v: "gemini/veo-3", r2v: "ark/seedance" } }}
          usesReferenceImages
        />,
      );
      expect(screen.getByRole("combobox", { name: "默认视频模型" })).toHaveTextContent("自动选择（当前：Ark · seedance）");
      expect(await screen.findByRole("radio", { name: "5 秒" })).toBeInTheDocument();
    });

    it("prefers a configured global bucket over the auto-selected model", async () => {
      render(
        <ModelConfigSection
          value={EMPTY_VALUE}
          onChange={() => {}}
          providers={PROVIDERS}
          options={OPTIONS}
          globalDefaults={{ ...EMPTY_GLOBALS, videoI2V: "gemini/veo-3", auto: AUTO }}
        />,
      );
      expect(screen.getByRole("combobox", { name: "默认视频模型" })).not.toHaveTextContent("自动选择");
      expect(await screen.findByRole("radio", { name: "4 秒" })).toBeInTheDocument();
      expect(screen.queryByRole("radio", { name: "5 秒" })).not.toBeInTheDocument();
    });

    it("says no provider is available when nothing can be auto-selected", () => {
      render(
        <ModelConfigSection
          value={EMPTY_VALUE}
          onChange={() => {}}
          providers={PROVIDERS}
          options={OPTIONS}
          globalDefaults={{ ...EMPTY_GLOBALS, auto: { ...AUTO, i2v: null } }}
        />,
      );
      expect(screen.getByRole("combobox", { name: "默认视频模型" })).toHaveTextContent("自动选择（没有可用的供应商）");
      expect(screen.queryByRole("radio", { name: "5 秒" })).not.toBeInTheDocument();
    });
  });

  it("renders slider when supported_durations is continuous integer range ≥ 5", async () => {
    const continuousProviders: ProviderInfo[] = [
      {
        id: "ark",
        display_name: "Ark",
        description: "",
        status: "ready",
        media_types: ["video"],
        capabilities: [],
        credential_count: 0,
        models: {
          seedance: {
            display_name: "seedance",
            media_type: "video",
            capabilities: [],
            default: false,
            supported_durations: [3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15],
            resolutions: [],
            qualities: [],
            audio_track: "controllable",
            reference_route_audio_track: "controllable",
            voice_consistency: "soft",
          },
        },
      },
    ];
    fakeCatalog = continuousProviders;
    render(
      <ModelConfigSection
        value={{ ...EMPTY_VALUE, videoBackend: "ark/seedance" }}
        onChange={() => {}}
        providers={continuousProviders}
        options={{ ...OPTIONS, videoBackends: ["ark/seedance"] }}
        globalDefaults={EMPTY_GLOBALS}
      />,
    );
    expect(await screen.findByRole("slider")).toBeInTheDocument();
    expect(screen.queryByRole("radio", { name: "3 秒" })).not.toBeInTheDocument();
  });

  it("hides duration picker when effective backend has no supported_durations", async () => {
    render(
      <ModelConfigSection
        value={{ ...EMPTY_VALUE, videoBackend: "unknown/no-such" }}
        onChange={() => {}}
        providers={PROVIDERS}
        options={{ ...OPTIONS, videoBackends: ["unknown/no-such"] }}
        globalDefaults={EMPTY_GLOBALS}
      />,
    );
    await waitFor(() => expect(API.getModelVideoCapabilities).toHaveBeenCalled());
    expect(screen.queryByRole("slider")).not.toBeInTheDocument();
    expect(screen.queryByRole("radio", { name: /^\d+ 秒$/ })).not.toBeInTheDocument();
  });

  it("marks 'auto' radio as checked when defaultDuration is null", async () => {
    render(
      <ModelConfigSection
        value={{ ...EMPTY_VALUE, videoBackend: "gemini/veo-3", defaultDuration: null }}
        onChange={() => {}}
        providers={PROVIDERS}
        options={OPTIONS}
        globalDefaults={EMPTY_GLOBALS}
      />,
    );
    expect(await screen.findByRole("radio", { name: "auto" })).toBeChecked();
  });

  it("marks the selected duration radio as checked", async () => {
    render(
      <ModelConfigSection
        value={{ ...EMPTY_VALUE, videoBackend: "gemini/veo-3", defaultDuration: 6 }}
        onChange={() => {}}
        providers={PROVIDERS}
        options={OPTIONS}
        globalDefaults={EMPTY_GLOBALS}
      />,
    );
    expect(await screen.findByRole("radio", { name: "6 秒" })).toBeChecked();
    expect(screen.getByRole("radio", { name: "4 秒" })).not.toBeChecked();
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
        globalDefaults={EMPTY_GLOBALS}
      />,
    );
    await user.click(await screen.findByRole("radio", { name: "6 秒" }));
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ defaultDuration: 6 }));
  });

  it("shows an out-of-range notice with no duration radio checked when saved duration is unsupported", async () => {
    render(
      <ModelConfigSection
        value={{ ...EMPTY_VALUE, videoBackend: "gemini/veo-3", defaultDuration: 10 }}
        onChange={() => {}}
        providers={PROVIDERS}
        options={OPTIONS}
        globalDefaults={EMPTY_GLOBALS}
      />,
    );
    expect(await screen.findByText(/不再受当前模型支持/)).toBeInTheDocument();
    expect(screen.getByText(/10/)).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "auto" })).not.toBeChecked();
    for (const sec of ["4 秒", "6 秒", "8 秒"]) {
      expect(screen.getByRole("radio", { name: sec })).not.toBeChecked();
    }
    expect(screen.getByRole("radio", { name: "auto" })).toHaveAttribute("tabindex", "0");
  });

  it("resets defaultDuration to null when the out-of-range reset action is clicked", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      <ModelConfigSection
        value={{ ...EMPTY_VALUE, videoBackend: "gemini/veo-3", defaultDuration: 10 }}
        onChange={onChange}
        providers={PROVIDERS}
        options={OPTIONS}
        globalDefaults={EMPTY_GLOBALS}
      />,
    );
    await user.click(await screen.findByRole("button", { name: "回退到 auto" }));
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ defaultDuration: null }));
  });

  it("does not show the out-of-range notice when saved duration is supported", async () => {
    render(
      <ModelConfigSection
        value={{ ...EMPTY_VALUE, videoBackend: "gemini/veo-3", defaultDuration: 6 }}
        onChange={() => {}}
        providers={PROVIDERS}
        options={OPTIONS}
        globalDefaults={EMPTY_GLOBALS}
      />,
    );
    await screen.findByRole("radio", { name: "6 秒" });
    expect(screen.queryByText(/不再受当前模型支持/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "回退到 auto" })).not.toBeInTheDocument();
  });

  it("shows the out-of-range notice and reset action under the slider branch too", async () => {
    const continuousProviders: ProviderInfo[] = [
      {
        id: "ark",
        display_name: "Ark",
        description: "",
        status: "ready",
        media_types: ["video"],
        capabilities: [],
        credential_count: 0,
        models: {
          seedance: {
            display_name: "seedance",
            media_type: "video",
            capabilities: [],
            default: false,
            supported_durations: [3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15],
            resolutions: [],
            qualities: [],
            audio_track: "controllable",
            reference_route_audio_track: "controllable",
            voice_consistency: "soft",
          },
        },
      },
    ];
    const user = userEvent.setup();
    const onChange = vi.fn();
    fakeCatalog = continuousProviders;
    render(
      <ModelConfigSection
        value={{ ...EMPTY_VALUE, videoBackend: "ark/seedance", defaultDuration: 20 }}
        onChange={onChange}
        providers={continuousProviders}
        options={{ ...OPTIONS, videoBackends: ["ark/seedance"] }}
        globalDefaults={EMPTY_GLOBALS}
      />,
    );
    const slider = await screen.findByRole("slider");
    expect(slider).toBeInTheDocument();
    expect(screen.getByText(/不再受当前模型支持/)).toBeInTheDocument();
    expect(slider).toHaveAttribute("aria-valuetext", expect.stringMatching(/20/));
    expect(slider.getAttribute("aria-valuetext")).not.toBe("auto");
    await user.click(screen.getByRole("button", { name: "回退到 auto" }));
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ defaultDuration: null }));
  });


  const VEO_PROVIDERS: ProviderInfo[] = [
    {
      id: "gemini-aistudio",
      display_name: "AI Studio",
      description: "",
      status: "ready",
      media_types: ["video"],
      capabilities: [],
      credential_count: 0,
      models: {
        veo: {
          display_name: "Veo 3.1",
          media_type: "video",
          capabilities: [],
          default: false,
          supported_durations: [4, 6, 8],
          resolutions: ["720p", "1080p", "4k"],
          qualities: [],
          audio_track: "controllable",
          reference_route_audio_track: "controllable",
          voice_consistency: "soft",
        },
      },
    },
  ];

  const VEO_OPTIONS = {
    videoBackends: ["gemini-aistudio/veo"],
    imageBackends: [],
    textBackends: [],
    providerNames: { "gemini-aistudio": "AI Studio" },
  };

  const NO_GLOBAL_DEFAULTS = EMPTY_GLOBALS;

  function renderVeo(
    overrides: Partial<React.ComponentProps<typeof ModelConfigSection>> & {
      videoResolution?: string | null;
      defaultDuration?: number | null;
    } = {},
  ) {
    const { videoResolution = null, defaultDuration = null, ...props } = overrides;
    fakeCatalog = VEO_PROVIDERS;
    return render(
      <ModelConfigSection
        value={{
          ...EMPTY_VALUE,
          videoBackend: "gemini-aistudio/veo",
          videoResolution,
          defaultDuration,
        }}
        onChange={() => {}}
        providers={VEO_PROVIDERS}
        options={VEO_OPTIONS}
        globalDefaults={NO_GLOBAL_DEFAULTS}
        {...props}
      />,
    );
  }

  it("offers every supported duration at an unconstrained resolution", async () => {
    renderVeo({ videoResolution: "720p" });
    await screen.findByRole("radio", { name: "4 秒" });
    for (const sec of ["4 秒", "6 秒", "8 秒"]) {
      expect(screen.getByRole("radio", { name: sec })).toBeInTheDocument();
    }
  });

  it.each(["1080p", "4k"])("offers only 8s at %s", async (resolution) => {
    renderVeo({ videoResolution: resolution });
    expect(await screen.findByRole("radio", { name: "8 秒" })).toBeInTheDocument();
    expect(screen.queryByRole("radio", { name: "4 秒" })).not.toBeInTheDocument();
    expect(screen.queryByRole("radio", { name: "6 秒" })).not.toBeInTheDocument();
    expect(API.getModelVideoCapabilities).toHaveBeenLastCalledWith(
      "gemini-aistudio/veo",
      expect.objectContaining({ resolution }),
    );
  });

  it("stops accepting duration选择 while the new context's constraints are still in flight", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    const gate: { release?: () => void } = {};
    const original = fakeVideoCapabilities;
    vi.spyOn(API, "getModelVideoCapabilities").mockImplementation((backend, query = {}) => {
      if (query.resolution !== "4k") return original(backend, query);
      return new Promise<VideoCapabilities>((resolve) => {
        gate.release = () => resolve(original(backend, query));
      });
    });

    const { rerender } = renderVeo({ videoResolution: "720p", onChange });
    await screen.findByRole("radio", { name: "4 秒" });

    rerender(
      <ModelConfigSection
        value={{ ...EMPTY_VALUE, videoBackend: "gemini-aistudio/veo", videoResolution: "4k", defaultDuration: null }}
        onChange={onChange}
        providers={VEO_PROVIDERS}
        options={VEO_OPTIONS}
        globalDefaults={NO_GLOBAL_DEFAULTS}
      />,
    );

    const group = await screen.findByRole("radiogroup", { name: "默认时长" });
    await waitFor(() => expect(group).toHaveAttribute("aria-disabled", "true"));
    await user.click(screen.getByRole("radio", { name: "4 秒" }));
    expect(onChange).not.toHaveBeenCalled();

    gate.release?.();
    expect(await screen.findByRole("radio", { name: "8 秒" })).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByRole("radio", { name: "4 秒" })).not.toBeInTheDocument());
    await user.click(screen.getByRole("radio", { name: "8 秒" }));
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ defaultDuration: 8 }));
  });

  it("offers only 8s on the reference-video path even at 720p", async () => {
    renderVeo({ videoResolution: "720p", usesReferenceImages: true });
    expect(await screen.findByRole("radio", { name: "8 秒" })).toBeInTheDocument();
    expect(screen.queryByRole("radio", { name: "4 秒" })).not.toBeInTheDocument();
    expect(API.getModelVideoCapabilities).toHaveBeenLastCalledWith(
      "gemini-aistudio/veo",
      expect.objectContaining({ resolution: "720p", usesReferenceImages: true }),
    );
  });

  it("sends the project name and an explicit auto resolution when editing a saved project", async () => {
    renderVeo({ projectName: "saved-project", videoResolution: null });
    await screen.findByRole("radio", { name: "4 秒" });
    expect(API.getVideoCapabilities).toHaveBeenLastCalledWith(
      "saved-project",
      expect.objectContaining({ videoBackend: "gemini-aistudio/veo", resolution: null }),
    );
    expect(API.getModelVideoCapabilities).not.toHaveBeenCalled();
  });

  it.each([
    ["1080p 分辨率", { videoResolution: "1080p" }, /当前分辨率下不可用/],
    ["参考生视频", { videoResolution: "720p", usesReferenceImages: true }, /参考生视频下不可用/],
  ])("warns about a saved 4s duration under %s", async (_label, overrides, expected) => {
    renderVeo({ ...overrides, defaultDuration: 4 });
    expect(await screen.findByRole("alert")).toHaveTextContent(expected);
    expect(screen.getByRole("alert")).not.toHaveTextContent(/不再受当前模型支持/);
    expect(screen.getByRole("radio", { name: "8 秒" })).not.toBeChecked();
  });

  it("keeps a saved 4s duration valid when neither constraint applies", async () => {
    renderVeo({ videoResolution: "720p", defaultDuration: 4 });
    expect(await screen.findByRole("radio", { name: "4 秒" })).toBeChecked();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});

describe("音频开关的模型可控性", () => {
  const AUDIO_PROVIDERS: ProviderInfo[] = [
    {
      id: "ark",
      display_name: "Ark",
      description: "",
      status: "ready",
      media_types: ["video"],
      capabilities: [],
      credential_count: 0,
      models: {
        seedance: {
          display_name: "seedance",
          media_type: "video",
          capabilities: [],
          default: false,
          supported_durations: [5],
          resolutions: [],
          qualities: [],
          audio_track: "controllable",
          reference_route_audio_track: "controllable",
          voice_consistency: "soft",
        },
      },
    },
    {
      id: "kling",
      display_name: "Kling",
      description: "",
      status: "ready",
      media_types: ["video"],
      capabilities: [],
      credential_count: 0,
      models: {
        "v3-omni": {
          display_name: "v3-omni",
          media_type: "video",
          capabilities: [],
          default: false,
          supported_durations: [5],
          resolutions: [],
          qualities: [],
          audio_track: "controllable",
          reference_route_audio_track: "always_off",
          voice_consistency: "soft",
        },
      },
    },
    {
      id: "dashscope",
      display_name: "DashScope",
      description: "",
      status: "ready",
      media_types: ["video"],
      capabilities: [],
      credential_count: 0,
      models: {
        wan: {
          display_name: "wan",
          media_type: "video",
          capabilities: [],
          default: false,
          supported_durations: [5],
          resolutions: [],
          qualities: [],
          audio_track: "always_on",
          reference_route_audio_track: "always_on",
          voice_consistency: "soft",
        },
      },
    },
    {
      id: "minimax",
      display_name: "MiniMax",
      description: "",
      status: "ready",
      media_types: ["video"],
      capabilities: [],
      credential_count: 0,
      models: {
        "hailuo-02": {
          display_name: "hailuo-02",
          media_type: "video",
          capabilities: [],
          default: false,
          supported_durations: [6],
          resolutions: [],
          qualities: [],
          audio_track: "always_off",
          reference_route_audio_track: "always_off",
          voice_consistency: "none",
        },
      },
    },
  ];

  function renderAudio(
    videoBackend: string,
    videoGenerateAudio: boolean | null,
    onVideoGenerateAudioChange = vi.fn(),
    globalVideoGenerateAudio = true,
    usesReferenceImages = false,
    customProviders: CustomProviderInfo[] = [],
  ) {
    render(
      <ModelConfigSection
        showSubFields={false}
        value={{ ...EMPTY_VALUE, videoBackend }}
        onChange={() => {}}
        providers={AUDIO_PROVIDERS}
        customProviders={customProviders}
        usesReferenceImages={usesReferenceImages}
        options={{
          videoBackends: ["ark/seedance", "dashscope/wan", "minimax/hailuo-02", "kling/v3-omni"],
          imageBackends: [],
          textBackends: [],
          providerNames: {},
        }}
        globalDefaults={EMPTY_GLOBALS}
        videoGenerateAudio={videoGenerateAudio}
        globalVideoGenerateAudio={globalVideoGenerateAudio}
        onVideoGenerateAudioChange={onVideoGenerateAudioChange}
        enable={{ image: false, text: false }}
      />,
    );
    return onVideoGenerateAudioChange;
  }

  it.each(["always_on", "always_off"] as const)("uses server audio facts for a custom %s model", (mode) => {
    const customProviders: CustomProviderInfo[] = [{
      id: 1, display_name: "Custom", discovery_format: "openai", base_url: "https://example.invalid",
      api_key_masked: "", created_at: "", image_max_workers: null, video_max_workers: null, audio_max_workers: null,
      models: [{
        id: 1, model_id: "video", display_name: "Video", endpoint: "dashscope-video",
        is_default: true, is_enabled: true, resolution: null, qualities: [], multiplier: 1,
        entry_binding: null, matched_entry: null, system_capabilities: null,
        capability_overrides: null, path_overrides: null, requests: [],
        audio_track: mode, reference_route_audio_track: mode,
        voice_consistency: mode === "always_off" ? "none" : "soft",
      }],
    }];
    renderAudio("custom-1/video", null, vi.fn(), true, false, customProviders);
    expect(screen.getByRole("radio", { name: "开启" })).toHaveAttribute("aria-disabled", "true");
    expect(screen.getByRole("radio", { name: mode === "always_on" ? "开启" : "关闭" })).toBeChecked();
    expect(screen.getByText(mode === "always_on" ? /始终带声音/ : /没有声音/)).toBeInTheDocument();
  });

  it("keeps the switch interactive for a model whose audio track is controllable", async () => {
    const user = userEvent.setup();
    const onChange = renderAudio("ark/seedance", null);
    const off = screen.getByRole("radio", { name: "关闭" });
    expect(off).toBeEnabled();
    await user.click(off);
    expect(onChange).toHaveBeenCalledWith(false);
    expect(screen.queryByText(/音频开关不可调整/)).not.toBeInTheDocument();
  });

  it("locks the switch on an always-audible model and shows the film is audible", () => {
    renderAudio("dashscope/wan", null);
    expect(screen.getByRole("radio", { name: "关闭" })).toHaveAttribute("aria-disabled", "true");
    expect(screen.getByRole("radio", { name: "开启" })).toBeChecked();
    expect(screen.getByText(/始终带声音/)).toBeInTheDocument();
  });

  it("locks the switch on a model without an audio track and shows the film is silent", () => {
    renderAudio("minimax/hailuo-02", null);
    expect(screen.getByRole("radio", { name: "开启" })).toHaveAttribute("aria-disabled", "true");
    expect(screen.getByRole("radio", { name: "关闭" })).toBeChecked();
    expect(screen.getByText(/没有声音/)).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("offers a one-click fix when a stored off setting contradicts an always-audible model", async () => {
    const user = userEvent.setup();
    const onChange = renderAudio("dashscope/wan", false);
    expect(screen.getByRole("alert")).toHaveTextContent(/无法关闭声音/);
    await user.click(screen.getByRole("button", { name: "改为开启" }));
    expect(onChange).toHaveBeenCalledWith(true);
  });

  it("offers the same fix when the project follows a global off setting", async () => {
    const user = userEvent.setup();
    const onChange = renderAudio("dashscope/wan", null, vi.fn(), false);
    expect(screen.getByRole("alert")).toHaveTextContent(/无法关闭声音/);
    await user.click(screen.getByRole("button", { name: "改为开启" }));
    expect(onChange).toHaveBeenCalledWith(true);
  });

  it("stays quiet when the project follows a global on setting", () => {
    renderAudio("dashscope/wan", null, vi.fn(), true);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("keeps the switch interactive for a route-split model on the first-frame route", () => {
    renderAudio("kling/v3-omni", null);
    expect(screen.getByRole("radio", { name: "关闭" })).toBeEnabled();
    expect(screen.queryByText(/没有声音/)).not.toBeInTheDocument();
  });

  it("locks the switch for the same model on the reference route and shows the film is silent", () => {
    renderAudio("kling/v3-omni", null, vi.fn(), true, true);
    expect(screen.getByRole("radio", { name: "开启" })).toHaveAttribute("aria-disabled", "true");
    expect(screen.getByRole("radio", { name: "关闭" })).toBeChecked();
    expect(screen.getByText(/没有声音/)).toBeInTheDocument();
  });

  describe("候选下拉的音轨能力线", () => {
    const OMNI_CANDIDATES = {
      image: {
        default: ["kling/v3-omni"],
        buckets: { t2i: ["kling/v3-omni"], i2i: ["kling/v3-omni"] },
        filtered: {},
      },
      video: {
        default: ["kling/v3-omni"],
        buckets: { i2v: ["kling/v3-omni"], r2v: ["kling/v3-omni"] },
        filtered: {},
      },
      provider_names: {},
      model_names: {},
      lifecycle: {},
    };

    function renderDropdowns(usesReferenceImages: boolean) {
      render(
        <ModelConfigSection
          value={EMPTY_VALUE}
          onChange={() => {}}
          providers={AUDIO_PROVIDERS}
          usesReferenceImages={usesReferenceImages}
          options={{
            videoBackends: ["kling/v3-omni"],
            imageBackends: ["kling/v3-omni"],
            textBackends: [],
            providerNames: { kling: "Kling" },
          }}
          candidates={OMNI_CANDIDATES}
          globalDefaults={EMPTY_GLOBALS}
          enable={{ text: false }}
        />,
      );
    }

    async function omniRowIn(user: ReturnType<typeof userEvent.setup>, comboboxName: string) {
      await user.click(screen.getByRole("combobox", { name: comboboxName }));
      const text = (await screen.findByRole("option", { name: /v3-omni/ })).textContent ?? "";
      await user.keyboard("{Escape}");
      return text;
    }

    it("参考生视频项目：默认层与参考生桶标无声，图生桶不受牵连仍标有声", async () => {
      const user = userEvent.setup();
      renderDropdowns(true);
      expect(await omniRowIn(user, "默认视频模型")).toContain("无声");

      await user.click(screen.getAllByText("按用途指定模型")[0]);
      expect(await omniRowIn(user, "参考生视频")).toContain("无声");
      expect(await omniRowIn(user, "图生视频")).toContain("有声");
    });

    it("图生视频项目：默认层与图生桶标有声，参考生桶仍标无声", async () => {
      const user = userEvent.setup();
      renderDropdowns(false);
      expect(await omniRowIn(user, "默认视频模型")).toContain("有声");

      await user.click(screen.getAllByText("按用途指定模型")[0]);
      expect(await omniRowIn(user, "图生视频")).toContain("有声");
      expect(await omniRowIn(user, "参考生视频")).toContain("无声");
    });

    it("图片侧的默认层与细分项下拉都不带音轨格——音轨是视频概念，图片桶不消费执行路径", async () => {
      const user = userEvent.setup();
      renderDropdowns(true);
      expect(await omniRowIn(user, "默认图片模型")).not.toMatch(/有声|无声/);

      await user.click(screen.getAllByText("按用途指定模型")[1]);
      expect(await omniRowIn(user, "文生图")).not.toMatch(/有声|无声/);
      expect(await omniRowIn(user, "图生图")).not.toMatch(/有声|无声/);
    });
  });
});

describe("dimensions a ComfyUI workflow fixes", () => {
  const COMFY_ENDPOINT: EndpointDescriptor = {
    key: "ce-2",
    media_type: "video",
    family: "custom",
    kind: "comfyui",
    source: "custom",
    display_name_key: "",
    display_name: "我的 Wan workflow",
    request_method: "POST",
    request_path_template: "/prompt",
    image_capabilities: null,
    end_image_capable: false,
    size_fixed: false,
    duration_fixed: false,
    duration_frame_rate_missing: false,
    duration_tier_empty: false,
    native_resolution: null,
    requests: [],
  };

  const COMFY_PROVIDER: CustomProviderInfo = {
    id: 3,
    display_name: "我的 ComfyUI",
    discovery_format: "comfyui",
    base_url: "http://comfy.invalid:8188",
    api_key_masked: "",
    created_at: "2026-01-01T00:00:00Z",
    image_max_workers: null,
    video_max_workers: null,
    audio_max_workers: null,
    models: [
      {
        id: 1,
        model_id: "my-wan-workflow",
        display_name: "My Workflow",
        endpoint: "ce-2",
        is_default: true,
        is_enabled: true,
        resolution: null,
        qualities: [],
        multiplier: 1,
        entry_binding: null,
        matched_entry: null,
        system_capabilities: null,
        capability_overrides: null,
        path_overrides: null,
        requests: [],
      },
    ],
  };

  const BACKEND = "custom-3/my-wan-workflow";

  function renderWithConstraints(overrides: Partial<EndpointDescriptor>, durations: number[]) {
    useEndpointCatalogStore.setState(useEndpointCatalogStore.getInitialState(), true);
    vi.spyOn(API, "listEndpointCatalog").mockResolvedValue({
      endpoints: [{ ...COMFY_ENDPOINT, ...overrides }],
    });
    const constraints: DurationConstraints = {
      resolution: null,
      uses_reference_images: false,
      allowed: durations,
      excluded: {},
    };
    vi.spyOn(API, "getModelVideoCapabilities").mockResolvedValue({
      provider_id: "custom-3",
      model: "my-wan-workflow",
      supported_durations: durations,
      max_duration: durations.length > 0 ? Math.max(...durations) : 0,
      max_reference_images: 0,
      first_frame: true,
      last_frame: false,
      source: "custom",
      voice_consistency: "soft",
      duration_constraints: constraints,
    });
    render(
      <ModelConfigSection
        value={{ ...EMPTY_VALUE, videoBackend: BACKEND }}
        onChange={() => {}}
        providers={[]}
        customProviders={[COMFY_PROVIDER]}
        options={{
          videoBackends: [BACKEND],
          imageBackends: [],
          textBackends: [],
          providerNames: { "custom-3": "我的 ComfyUI" },
        }}
        globalDefaults={EMPTY_GLOBALS}
        enable={{ image: false, text: false }}
      />,
    );
  }

  it("disables the resolution picker and names the native tier when the size is fixed", async () => {
    renderWithConstraints({ size_fixed: true, native_resolution: "480p" }, [5]);

    const picker = await screen.findByRole("combobox", { name: "分辨率" });
    expect(picker).toBeDisabled();
    expect(picker).toHaveAttribute("placeholder", "workflow 原生（480p）");
    expect(screen.getByText(/此 workflow 的像素分辨率固定：宽高没有同时绑定到节点/)).toBeInTheDocument();
  });

  it("says the workflow decides the size when there is no literal to name", async () => {
    renderWithConstraints({ size_fixed: true, native_resolution: null }, [5]);

    const picker = await screen.findByRole("combobox", { name: "分辨率" });
    expect(picker).toBeDisabled();
    expect(picker).toHaveAttribute("placeholder", "尺寸由 workflow 决定");
  });

  it("keeps the resolution picker usable while still naming the native tier", async () => {
    renderWithConstraints({ native_resolution: "720p" }, [5]);

    const picker = await screen.findByRole("combobox", { name: "分辨率" });
    expect(picker).toBeEnabled();
    expect(picker).toHaveAttribute("placeholder", "workflow 原生（720p）");
  });

  it("says why the duration control is absent when the workflow fixes its duration", async () => {
    renderWithConstraints({ duration_fixed: true, duration_tier_empty: true }, []);

    expect(await screen.findByText(/时长由端点固定/)).toBeInTheDocument();
    expect(screen.queryByRole("radiogroup", { name: "默认时长" })).not.toBeInTheDocument();
  });

  it("says why the duration control is absent when the frame rate cannot be read either", async () => {
    renderWithConstraints({ duration_fixed: false, duration_tier_empty: true }, []);

    expect(await screen.findByText(/时长由端点固定/)).toBeInTheDocument();
    expect(screen.queryByRole("radiogroup", { name: "默认时长" })).not.toBeInTheDocument();
  });

  it("renders the duration control as usual when the tier is not empty", async () => {
    renderWithConstraints({}, [5]);

    expect(await screen.findByRole("radiogroup", { name: "默认时长" })).toBeInTheDocument();
    expect(screen.queryByText(/时长由端点固定/)).not.toBeInTheDocument();
  });
});

describe("图片画质与失效的已存值", () => {
  function imageModel(resolutions: string[], qualities: string[]): ModelInfoResponse {
    return {
      display_name: "image",
      media_type: "image",
      capabilities: [],
      default: false,
      supported_durations: [],
      resolutions,
      qualities,
      audio_track: "always_off",
      reference_route_audio_track: "always_off",
      voice_consistency: "none",
    };
  }

  const IMAGE_PROVIDERS: ProviderInfo[] = [
    ...PROVIDERS,
    {
      id: "openai",
      display_name: "OpenAI",
      description: "",
      status: "ready",
      media_types: ["image"],
      capabilities: [],
      credential_count: 0,
      models: {
        "gpt-image-2": imageModel(["1K", "2K"], ["low", "medium", "high"]),
        "gpt-image-1": imageModel(["1K", "2K"], []),
        "gpt-image-legacy": imageModel(["512px", "1K", "2K"], []),
      },
    },
  ];

  const CUSTOM_IMAGE_PROVIDER: CustomProviderInfo = {
    id: 7,
    display_name: "自建网关",
    discovery_format: "openai",
    base_url: "https://gateway.invalid",
    api_key_masked: "",
    created_at: "2026-01-01T00:00:00Z",
    image_max_workers: null,
    video_max_workers: null,
    audio_max_workers: null,
    models: [
      {
        id: 1,
        model_id: "gpt-image-2",
        display_name: "GPT Image 2",
        endpoint: "openai-images",
        is_default: true,
        is_enabled: true,
        resolution: null,
        qualities: ["medium", "high"],
        multiplier: 1,
        entry_binding: null,
        matched_entry: null,
        system_capabilities: null,
        capability_overrides: null,
        path_overrides: null,
        requests: [],
      },
    ],
  };

  function renderImage(value: Partial<ComponentProps<typeof ModelConfigSection>["value"]>, onChange = vi.fn()) {
    render(
      <ModelConfigSection
        value={{ ...EMPTY_VALUE, ...value }}
        onChange={onChange}
        providers={IMAGE_PROVIDERS}
        customProviders={[CUSTOM_IMAGE_PROVIDER]}
        options={{
          videoBackends: [],
          imageBackends: ["openai/gpt-image-2", "openai/gpt-image-1", "custom-7/gpt-image-2"],
          textBackends: [],
          providerNames: { openai: "OpenAI", "custom-7": "自建网关" },
        }}
        globalDefaults={EMPTY_GLOBALS}
        enable={{ video: false, text: false }}
      />,
    );
    return onChange;
  }

  beforeEach(() => {
    useEndpointCatalogStore.setState(
      { ...useEndpointCatalogStore.getInitialState(), endpointToMediaType: { "openai-images": "image" } },
      true,
    );
    vi.spyOn(API, "listEndpointCatalog").mockResolvedValue({ endpoints: [] });
  });

  it("offers the executing model's qualities in API order after an auto entry, and writes the pick", async () => {
    const user = userEvent.setup();
    const onChange = renderImage({ imageBackendDefault: "openai/gpt-image-2" });

    const picker = screen.getByRole("combobox", { name: "画质" });
    expect(picker).toHaveTextContent("自动（按分辨率档位）");
    await user.click(picker);
    const options = await screen.findAllByRole("option");
    expect(options.map((o) => o.textContent)).toEqual(["自动（按分辨率档位）", "low", "medium", "high"]);
    await user.click(screen.getByRole("option", { name: "high" }));
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ imageQuality: "high" }));
  });

  it("writes null when the auto entry is picked again", async () => {
    const user = userEvent.setup();
    const onChange = renderImage({ imageBackendDefault: "openai/gpt-image-2", imageQuality: "medium" });

    await user.click(screen.getByRole("combobox", { name: "画质" }));
    await user.click(await screen.findByRole("option", { name: "自动（按分辨率档位）" }));
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ imageQuality: null }));
  });

  it("does not render the quality field when the executing model has no qualities", () => {
    renderImage({ imageBackendDefault: "openai/gpt-image-1", imageQuality: "high" });

    expect(screen.getByRole("combobox", { name: "分辨率" })).toBeInTheDocument();
    expect(screen.queryByRole("combobox", { name: "画质" })).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("uses a dropdown for a custom provider's qualities too", async () => {
    const user = userEvent.setup();
    renderImage({ imageBackendDefault: "custom-7/gpt-image-2" });

    await user.click(screen.getByRole("combobox", { name: "画质" }));
    const options = await screen.findAllByRole("option");
    expect(options.map((o) => o.textContent)).toEqual(["自动（按分辨率档位）", "medium", "high"]);
  });

  it("flags a stored quality that only differs in case, keeps it until the reset is clicked", async () => {
    const user = userEvent.setup();
    const onChange = renderImage({ imageBackendDefault: "openai/gpt-image-2", imageQuality: "LOW" });

    expect(screen.getByRole("combobox", { name: "画质" })).toHaveTextContent("LOW");
    expect(screen.getByRole("alert")).toHaveTextContent("已保存的画质 LOW 不在当前模型的可选值中");
    expect(onChange).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "恢复自动" }));
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ imageQuality: null }));
  });

  it("accepts a stored quality that only carries surrounding whitespace", () => {
    renderImage({ imageBackendDefault: "openai/gpt-image-2", imageQuality: " low " });

    expect(screen.getByRole("combobox", { name: "画质" })).toHaveTextContent("low");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("flags a stored image resolution the model no longer offers, keeps it until the reset is clicked", async () => {
    const user = userEvent.setup();
    const onChange = renderImage({ imageBackendDefault: "openai/gpt-image-2", imageResolution: "512px" });

    expect(screen.getByRole("combobox", { name: "分辨率" })).toHaveTextContent("512px");
    expect(screen.getByRole("alert")).toHaveTextContent("已保存的分辨率 512px 不在当前模型的可选档位中");
    expect(onChange).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "恢复默认" }));
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ imageResolution: null }));
  });

  it("keeps 512px selectable without a notice while the model data still offers it", () => {
    renderImage({ imageBackendDefault: "openai/gpt-image-legacy", imageResolution: "512px" });

    expect(screen.getByRole("combobox", { name: "分辨率" })).toHaveTextContent("512px");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("treats a stored resolution that differs only in case or whitespace as valid", () => {
    renderImage({ imageBackendDefault: "openai/gpt-image-2", imageResolution: " 2k " });

    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("does not flag a free-form resolution in a custom provider's input", () => {
    renderImage({ imageBackendDefault: "custom-7/gpt-image-2", imageResolution: "1536x1024" });

    expect(screen.getByRole("combobox", { name: "分辨率" })).toHaveValue("1536x1024");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("leaves the video resolution field without a stale notice", async () => {
    fakeCatalog = [
      {
        ...PROVIDERS[0],
        models: { "veo-3": { ...PROVIDERS[0].models["veo-3"], resolutions: ["720p", "1080p"] } },
      },
    ];
    render(
      <ModelConfigSection
        value={{ ...EMPTY_VALUE, videoBackend: "gemini/veo-3", videoResolution: "4k" }}
        onChange={() => {}}
        providers={fakeCatalog}
        options={OPTIONS}
        globalDefaults={EMPTY_GLOBALS}
        enable={{ image: false, text: false }}
      />,
    );

    expect(screen.getByRole("combobox", { name: "分辨率" })).toHaveTextContent("4k");
    await screen.findByRole("radio", { name: "8 秒" });
    expect(screen.queryByText(/已保存的分辨率/)).not.toBeInTheDocument();
  });
});

describe("ModelConfigSection – split t2i/i2i image resolutions", () => {
  const IMAGE_PROVIDERS: ProviderInfo[] = [
    {
      id: "gemini",
      display_name: "Gemini",
      description: "",
      status: "ready",
      media_types: ["image"],
      capabilities: [],
      credential_count: 0,
      models: {
        "nano-banana": {
          display_name: "Nano Banana",
          media_type: "image",
          capabilities: [],
          default: true,
          supported_durations: [],
          resolutions: ["1K", "2K", "4K"],
          qualities: [],
          audio_track: "always_off",
          reference_route_audio_track: "always_off",
          voice_consistency: "none",
        },
        "no-res-model": {
          display_name: "No Res",
          media_type: "image",
          capabilities: [],
          default: false,
          supported_durations: [],
          resolutions: [],
          qualities: [],
          audio_track: "always_off",
          reference_route_audio_track: "always_off",
          voice_consistency: "none",
        },
      },
    },
    {
      id: "openai",
      display_name: "OpenAI",
      description: "",
      status: "ready",
      media_types: ["image"],
      capabilities: [],
      credential_count: 0,
      models: {
        "gpt-image-2": {
          display_name: "GPT Image 2",
          media_type: "image",
          capabilities: [],
          default: true,
          supported_durations: [],
          resolutions: ["1K", "2K"],
          qualities: [],
          audio_track: "always_off",
          reference_route_audio_track: "always_off",
          voice_consistency: "none",
        },
      },
    },
  ];

  const IMAGE_OPTIONS = {
    videoBackends: [],
    imageBackends: ["gemini/nano-banana", "gemini/no-res-model", "openai/gpt-image-2"],
    textBackends: [],
    providerNames: { gemini: "Gemini", openai: "OpenAI" },
  };

  it("shows separate t2i and i2i resolution pickers with each model's own options when executing models differ", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      <ModelConfigSection
        value={{
          ...EMPTY_VALUE,
          imageBackendDefault: "gemini/nano-banana",
          imageBackendI2I: "openai/gpt-image-2",
          imageResolution: "4K",
          imageResolutions: { "gemini/nano-banana": "4K", "openai/gpt-image-2": "1K" },
        }}
        onChange={onChange}
        providers={IMAGE_PROVIDERS}
        options={IMAGE_OPTIONS}
        globalDefaults={EMPTY_GLOBALS}
        enable={{ video: false, text: false }}
      />,
    );

    const t2iPicker = screen.getByRole("combobox", { name: "文生图 · 分辨率" });
    const i2iPicker = screen.getByRole("combobox", { name: "图生图 · 分辨率" });
    expect(t2iPicker).toHaveTextContent("4K");
    expect(i2iPicker).toHaveTextContent("1K");

    await user.click(i2iPicker);
    expect(await screen.findByRole("option", { name: "1K" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "2K" })).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: "4K" })).not.toBeInTheDocument();

    await user.click(screen.getByRole("option", { name: "2K" }));
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({
        imageResolution: "4K",
        imageResolutions: { "gemini/nano-banana": "4K", "openai/gpt-image-2": "2K" },
      }),
    );
  });

  it("shows only one resolution picker when t2i and i2i resolve to the same model", () => {
    render(
      <ModelConfigSection
        value={{
          ...EMPTY_VALUE,
          imageBackendDefault: "gemini/nano-banana",
          imageResolution: "2K",
          imageResolutions: { "gemini/nano-banana": "2K" },
        }}
        onChange={() => {}}
        providers={IMAGE_PROVIDERS}
        options={IMAGE_OPTIONS}
        globalDefaults={EMPTY_GLOBALS}
        enable={{ video: false, text: false }}
      />,
    );

    expect(screen.getByRole("combobox", { name: "分辨率" })).toHaveTextContent("2K");
    expect(screen.queryByRole("combobox", { name: "文生图 · 分辨率" })).not.toBeInTheDocument();
    expect(screen.queryByRole("combobox", { name: "图生图 · 分辨率" })).not.toBeInTheDocument();
  });

  it("splits image resolutions when global defaults resolve t2i and i2i to different models", () => {
    render(
      <ModelConfigSection
        value={{
          ...EMPTY_VALUE,
          imageResolutions: { "gemini/nano-banana": "4K", "openai/gpt-image-2": "2K" },
        }}
        onChange={() => {}}
        providers={IMAGE_PROVIDERS}
        options={IMAGE_OPTIONS}
        globalDefaults={{
          ...EMPTY_GLOBALS,
          imageT2I: "gemini/nano-banana",
          imageI2I: "openai/gpt-image-2",
        }}
        enable={{ video: false, text: false }}
      />,
    );

    expect(screen.getByRole("combobox", { name: "文生图 · 分辨率" })).toHaveTextContent("4K");
    expect(screen.getByRole("combobox", { name: "图生图 · 分辨率" })).toHaveTextContent("2K");
  });

  it("renders only the supported bucket picker when the other executing image model has no resolutions", () => {
    render(
      <ModelConfigSection
        value={{
          ...EMPTY_VALUE,
          imageBackendDefault: "gemini/no-res-model",
          imageBackendI2I: "openai/gpt-image-2",
          imageResolutions: { "openai/gpt-image-2": "2K" },
        }}
        onChange={() => {}}
        providers={IMAGE_PROVIDERS}
        options={IMAGE_OPTIONS}
        globalDefaults={EMPTY_GLOBALS}
        enable={{ video: false, text: false }}
      />,
    );

    expect(screen.queryByRole("combobox", { name: "文生图 · 分辨率" })).not.toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "图生图 · 分辨率" })).toHaveTextContent("2K");
  });

  it("uses free-form combobox input for a custom i2i model while keeping builtin t2i as a select", () => {
    useEndpointCatalogStore.setState({
      endpointToMediaType: { "openai-images": "image" },
      endpointConstraints: {},
      initialized: true,
    });
    const customProviders: CustomProviderInfo[] = [
      {
        id: 7,
        display_name: "Relay",
        discovery_format: "openai",
        base_url: "https://relay.example/v1",
        api_key_masked: "sk-***",
        created_at: "2026-01-01T00:00:00Z",
        image_max_workers: null,
        video_max_workers: null,
        audio_max_workers: null,
        models: [
          {
            id: 1,
            model_id: "custom-edit",
            display_name: "Custom Edit",
            endpoint: "openai-images",
            is_default: false,
            is_enabled: true,
            resolution: null,
            qualities: [],
            multiplier: 1,
            entry_binding: null,
            matched_entry: null,
            system_capabilities: null,
            capability_overrides: null,
            path_overrides: null,
            requests: [],
          },
        ],
      },
    ];
    render(
      <ModelConfigSection
        value={{
          ...EMPTY_VALUE,
          imageBackendDefault: "gemini/nano-banana",
          imageBackendI2I: "custom-7/custom-edit",
          imageResolution: "4K",
          imageResolutions: { "gemini/nano-banana": "4K", "custom-7/custom-edit": "1536x1024" },
        }}
        onChange={() => {}}
        providers={IMAGE_PROVIDERS}
        customProviders={customProviders}
        options={{
          ...IMAGE_OPTIONS,
          imageBackends: [...IMAGE_OPTIONS.imageBackends, "custom-7/custom-edit"],
          providerNames: { ...IMAGE_OPTIONS.providerNames, "custom-7": "Relay" },
        }}
        globalDefaults={EMPTY_GLOBALS}
        enable={{ video: false, text: false }}
      />,
    );

    expect(screen.getByRole("combobox", { name: "文生图 · 分辨率" })).toHaveTextContent("4K");
    expect(screen.getByRole("combobox", { name: "图生图 · 分辨率" })).toHaveValue("1536x1024");
  });
});
