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
} as const;

const EMPTY_GLOBALS = {
  video: "", videoI2V: "", videoR2V: "",
  image: "", imageT2I: "", imageI2I: "",
  textDefault: "", textSimple: "", textComplex: "",
} as const;

// Server video-capabilities fake: full durations use current-test catalogs, Veo constraints return contextual
// presets (narrowing rules live only in backend lib/config/resolver.py; fixed answers, never recomputation).
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
    // Creation wizards retain only three primary default-layer selects: video + image + text.
    const comboboxes = screen.getAllByRole("combobox");
    expect(comboboxes).toHaveLength(3);
    expect(screen.queryByText("按用途指定模型")).not.toBeInTheDocument();

    // Opening each dropdown should reveal "使用全局默认" as the default option
    await user.click(comboboxes[0]);
    expect(await screen.findByRole("option", { name: /使用全局默认/ })).toBeInTheDocument();
    // Close by clicking again
    await user.click(comboboxes[0]);
  });

  it("keeps text tiers when media candidates are unavailable", async () => {
    // Candidate failures pass candidates=null; text tiers do not use these data and must remain.
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
    // Only text collapsible regions remain; video/image subdivisions lack candidates and hide.
    const disclosures = screen.getAllByRole("button", { name: /按用途指定模型/ });
    expect(disclosures).toHaveLength(1);
    await userEvent.setup().click(disclosures[0]);
    expect(screen.getByRole("combobox", { name: "简单任务" })).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "复杂任务" })).toBeInTheDocument();
  });

  it("keeps configured sub-fields visible and clearable when candidates are unavailable", async () => {
    // Candidate failures must not hide saved overrides, still effective server-side; hiding prevents visibility/clearing.
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

    // Configured text-to-image remains with saved values; unconfigured image-to-image lacks candidates and hides.
    const t2i = screen.getByRole("combobox", { name: "文生图" });
    expect(t2i).toHaveTextContent("nano-banana");
    expect(screen.queryByRole("combobox", { name: "图生图" })).not.toBeInTheDocument();

    // Clearing overrides requires no candidate data.
    await user.click(t2i);
    await user.click(await screen.findByRole("option", { name: /跟随默认/ }));
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ imageBackendT2I: "" }));
  });

  it("shows an explicit error notice with a retry entry when candidatesError is set, even with no saved overrides", async () => {
    // Fetch failures differ from Still loading (candidates=null without failure); failures require perceptible error signals.
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
    expect(alerts).toHaveLength(2); // video + image; text tiers use no candidates and do not participate.
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
      // Three media channels each have initially collapsed subdivisions.
      const sections = screen.getAllByRole("button", { name: /按用途指定模型/ });
      expect(sections).toHaveLength(3);
      expect(sections.every((d) => d.getAttribute("aria-expanded") === "false")).toBe(true);

      for (const summary of screen.getAllByText("按用途指定模型")) {
        await user.click(summary);
      }
      for (const name of ["图生视频", "参考生视频", "文生图", "图生图", "简单任务", "复杂任务"]) {
        expect(screen.getByRole("combobox", { name })).toBeInTheDocument();
      }
      // UI copy contains no internal terminology.
      expect(document.body).not.toHaveTextContent(/能力桶|任务类型桶|capability bucket/i);
    });

    it("feeds each sub-field from its own filtered candidate list while the default layer stays unfiltered", async () => {
      const user = userEvent.setup();
      renderWithCandidates();
      await user.click(screen.getAllByText("按用途指定模型")[0]);

      // Defaults remain unfiltered, containing both video models.
      await user.click(screen.getByRole("combobox", { name: "默认视频模型" }));
      expect(await screen.findByRole("option", { name: /veo-3/ })).toBeInTheDocument();
      expect(await screen.findByRole("option", { name: /seedance/ })).toBeInTheDocument();
      await user.keyboard("{Escape}");

      // Image-to-video buckets contain i2v candidates only.
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
              // Successors qualify only for this reference-to-video location.
              switch_targets: { default: null, i2v: null, r2v: "ark/seedance-2" },
            },
          },
        },
      });

      expect(screen.getAllByText(/即将下线（2026-11-24），官方建议改用/)).toHaveLength(2);
      await user.click(screen.getByRole("button", { name: "切换到 seedance-2" }));

      expect(onChange).toHaveBeenLastCalledWith(
        expect.objectContaining({ videoBackend: "ark/seedance", videoProviderR2V: "ark/seedance-2" }),
      );
    });

    describe("所选视频模型已下线", () => {
      const DISCONTINUED_ERROR = "Ark/seedance：该模型已于 2026-11-01 下线，官方建议改用 Seedance 2；请在设置中重新选择模型";
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

        expect(await screen.findByText("已下线（2026-11-01），无法继续生成，官方建议改用 Seedance 2")).toBeInTheDocument();
        await waitFor(() => expect(API.getVideoCapabilities).toHaveBeenCalled());
        await waitFor(() => expect(screen.getAllByRole("alert")).toHaveLength(1));
        expect(screen.queryByText(DISCONTINUED_ERROR)).not.toBeInTheDocument();
      });

      it("keeps the capability error when the model is executed without a notice here", async () => {
        // Projects follow global defaults; local references are empty without retirement notices, making errors the sole explanation.
        renderWithCandidates({
          projectName: "demo",
          globalDefaults: { ...EMPTY_GLOBALS, video: "ark/seedance" },
          candidates: discontinued(),
        });

        expect(await screen.findByText(DISCONTINUED_ERROR)).toBeInTheDocument();
      });

      it("keeps the capability error when only another generation path references the model", async () => {
        // Image-to-video inherits this global model; only reference-to-video explicitly refers to it here, whose notice concerns another path.
        renderWithCandidates({
          projectName: "demo",
          value: { ...EMPTY_VALUE, videoProviderR2V: "ark/seedance" },
          globalDefaults: { ...EMPTY_GLOBALS, video: "ark/seedance" },
          candidates: discontinued(),
        });

        expect(await screen.findByText(DISCONTINUED_ERROR)).toBeInTheDocument();
      });

      it("keeps the capability error next to a notice that only announces the shutdown", async () => {
        // Retiring models remain callable; capability errors describe different issues and do not duplicate notices.
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
      // Global r2v buckets use bucket values; absent i2v buckets use global defaults.
      expect(screen.getByRole("combobox", { name: "参考生视频" })).toHaveTextContent(
        /跟随默认 · Ark · seedance/,
      );
      expect(screen.getByRole("combobox", { name: "图生视频" })).toHaveTextContent(
        /跟随默认 · Gemini · veo-3/,
      );
    });

    it("revalidates duration and resolution when a sub-field switches the executing model", async () => {
      // Subdivision changes also change execution models; resolutions lack out-of-range fallback hints and must clear here.
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
        // Put different models in i2v buckets to exercise execution-bucket model changes.
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
          // Projects use image-to-video; r2v is not execution buckets, so execution models stay unchanged and both values remain.
          defaultDuration: 4,
          videoResolution: "1080p",
        }),
      );

      // Execution buckets change: clear resolution; 4 seconds are unsupported by seedance, so duration becomes Automatic.
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

    it("clears the resolution when a sub-field change moves the executing image model", async () => {
      const user = userEvent.setup();
      const onChange = vi.fn();
      renderWithCandidates({
        value: { ...EMPTY_VALUE, imageBackendDefault: "openai/gpt-image-edit", imageResolution: "1080p" },
        onChange,
      });
      await user.click(screen.getAllByText("按用途指定模型")[1]);
      await user.click(screen.getByRole("combobox", { name: "文生图" }));
      await user.click(await screen.findByRole("option", { name: /nano-banana/ }));
      expect(onChange).toHaveBeenCalledWith(
        expect.objectContaining({ imageBackendT2I: "gemini/nano-banana", imageResolution: null }),
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
    // Default veo-3 (4/6/8) overridden by image-to-video seedance (5/8/10); execution uses the latter,
    // so default-layer duration lists would save runtime-rejected values.
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

    // Reference-to-video projects use r2v buckets, unaffected by i2v overrides.
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
    // Execution models remain seedance; durations/resolutions must not reset.
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
    // Open the video backend dropdown
    const videoTrigger = screen.getByRole("combobox", { name: /视频模型/ });
    await user.click(videoTrigger);
    // Click on the ark/seedance option (4s is not in its supported_durations: [5, 8, 10])
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
        defaultDuration: 8, // 8 is in both supported lists
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
    // Without projectName (global settings), tiers use server-derived catalog-endpoint values without frontend inference.
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
    // No combobox for video model should be visible
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
    // Should reflect ark/seedance's supported_durations [5, 8, 10]
    expect(await screen.findByRole("radio", { name: "5 秒" })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "8 秒" })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "10 秒" })).toBeInTheDocument();
    // Should NOT show DEFAULT_DURATIONS buttons that ark/seedance doesn't support
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
    // No [4,6,8] fallback; omit entire duration cards. Without candidate models, no capabilities exist to query, so send no requests.
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
    // Continuous ranges use sliders, without button groups beyond auto/slider radios.
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
    // Out-of-range hints identify invalid seconds (10 outside gemini/veo-3 [4,6,8]).
    expect(await screen.findByText(/不再受当前模型支持/)).toBeInTheDocument();
    expect(screen.getByText(/10/)).toBeInTheDocument();
    // No duration buttons active: auto/all numeric aria-checked values are false.
    expect(screen.getByRole("radio", { name: "auto" })).not.toBeChecked();
    for (const sec of ["4 秒", "6 秒", "8 秒"]) {
      expect(screen.getByRole("radio", { name: sec })).not.toBeChecked();
    }
    // Out-of-range auto provides focusable fallbacks; keyboard Tab still reaches radiogroup reselection (no tabIndex=0 would trap keyboards).
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
    // Slider branch: 20 outside [3..15].
    const slider = await screen.findByRole("slider");
    expect(slider).toBeInTheDocument();
    expect(screen.getByText(/不再受当前模型支持/)).toBeInTheDocument();
    // Out-of-range readouts/aria-valuetext preserve original values rather than falsely reporting auto, consistent with inactive auto buttons/
    // second-specific out-of-range hints.
    expect(slider).toHaveAttribute("aria-valuetext", expect.stringMatching(/20/));
    expect(slider.getAttribute("aria-valuetext")).not.toBe("auto");
    await user.click(screen.getByRole("button", { name: "回退到 auto" }));
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ defaultDuration: null }));
  });

  // ── Coupled constraints: resolution/reference paths narrow durations ──────────────────

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
    // Pass unsaved form resolutions explicitly to servers instead of waiting for saves.
    expect(API.getModelVideoCapabilities).toHaveBeenLastCalledWith(
      "gemini-aistudio/veo",
      expect.objectContaining({ resolution }),
    );
  });

  it("stops accepting duration选择 while the new context's constraints are still in flight", async () => {
    // Changed constraint contexts with unchanged models retain previous narrowed results without loading flashes, but options belong to old tiers.
    // Controls display only during this window, preventing users choosing 4 seconds for 4K from 720p lists.
    const user = userEvent.setup();
    const onChange = vi.fn();
    // Store gates inside objects: let+closure assignment leaves TS control flow narrowed to null at use sites.
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

    // Old options stay without layout jumps, but entire groups are unavailable and clicking writes nothing.
    const group = await screen.findByRole("radiogroup", { name: "默认时长" });
    await waitFor(() => expect(group).toHaveAttribute("aria-disabled", "true"));
    await user.click(screen.getByRole("radio", { name: "4 秒" }));
    expect(onChange).not.toHaveBeenCalled();

    gate.release?.();
    // New contexts restore selection, retaining only 4K-allowed tiers.
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
    // Form Automatic passes explicit null; servers must not fall back to saved project tiers for narrowing.
    expect(API.getVideoCapabilities).toHaveBeenLastCalledWith(
      "saved-project",
      expect.objectContaining({ videoBackend: "gemini-aistudio/veo", resolution: null }),
    );
    expect(API.getModelVideoCapabilities).not.toHaveBeenCalled();
  });

  // Warning copy follows causes: models still support 4 seconds, so Model unsupported would misdirect users to replace models.
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
          audio_track: "controllable",
          reference_route_audio_track: "controllable",
          voice_consistency: "soft",
        },
      },
    },
    {
      // Kling v3-omni: image-to-video subpaths include audio toggles; reference-to-video native schemas lack those fields.
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
  ) {
    render(
      <ModelConfigSection
        showSubFields={false}
        value={{ ...EMPTY_VALUE, videoBackend }}
        onChange={() => {}}
        providers={AUDIO_PROVIDERS}
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

  // Existing Off configurations with always-audio models require visible correction paths after disabling, otherwise settings cannot be corrected.
  it("offers a one-click fix when a stored off setting contradicts an always-audible model", async () => {
    const user = userEvent.setup();
    const onChange = renderAudio("dashscope/wan", false);
    expect(screen.getByRole("alert")).toHaveTextContent(/无法关闭声音/);
    await user.click(screen.getByRole("button", { name: "改为开启" }));
    expect(onChange).toHaveBeenCalledWith(true);
  });

  // Project empty means Follow global; global Off also conflicts with always-audio models, so hints use effective values,
  // otherwise UI shows only disabled On, hiding contradictions until enqueue rejection.
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

  // Audio differs by execution path: the same model controls image-to-video audio but accepts no reference-to-video toggle. Model-only
  // lookup would let reference-to-video users enable audio and receive silent outputs without any notice.
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

  // Three simultaneous video selects follow three paths: defaults use project execution; subdivisions each use their buckets.
  // Audio cells/toggles must agree; conflicting answers would mislead users selecting models from previews.
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
            // Image selects deliberately use identical models, exposing wrongly connected audio cells.
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

    /** Open selects, read v3-omni row capabilities, then close; only one select opens at a time. */
    async function omniRowIn(user: ReturnType<typeof userEvent.setup>, comboboxName: string) {
      await user.click(screen.getByRole("combobox", { name: comboboxName }));
      const text = (await screen.findByRole("option", { name: /v3-omni/ })).textContent ?? "";
      await user.keyboard("{Escape}");
      return text;
    }

    it("参考生视频项目：默认层与参考生桶标无声，图生桶不受牵连仍标有声", async () => {
      const user = userEvent.setup();
      renderDropdowns(true);
      // Empty default layers execute r2v buckets; capability lines follow.
      expect(await omniRowIn(user, "默认视频模型")).toContain("无声");

      await user.click(screen.getAllByText("按用途指定模型")[0]);
      expect(await omniRowIn(user, "参考生视频")).toContain("无声");
      // Reference-to-video projects must not relabel Image-to-video cells, which describe image-to-video paths themselves.
      expect(await omniRowIn(user, "图生视频")).toContain("有声");
    });

    it("图生视频项目：默认层与图生桶标有声，参考生桶仍标无声", async () => {
      const user = userEvent.setup();
      renderDropdowns(false);
      expect(await omniRowIn(user, "默认视频模型")).toContain("有声");

      await user.click(screen.getAllByText("按用途指定模型")[0]);
      expect(await omniRowIn(user, "图生视频")).toContain("有声");
      // Projects currently outside reference-to-video still show reference-path capabilities here, independent of project settings.
      expect(await omniRowIn(user, "参考生视频")).toContain("无声");
    });

    it("图片侧的默认层与细分项下拉都不带音轨格——音轨是视频概念，图片桶不消费执行路径", async () => {
      const user = userEvent.setup();
      renderDropdowns(true);
      expect(await omniRowIn(user, "默认图片模型")).not.toMatch(/有声|无声/);

      // Image collapsible regions follow video.
      await user.click(screen.getAllByText("按用途指定模型")[1]);
      expect(await omniRowIn(user, "文生图")).not.toMatch(/有声|无声/);
      expect(await omniRowIn(user, "图生图")).not.toMatch(/有声|无声/);
    });
  });
});

// -------------------------------------------------------------------------
// ComfyUI model rows: workflow-fixed sizes/durations disable controls and explain reasons.
// -------------------------------------------------------------------------
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

  /** Projects using ComfyUI model rows; endpoint catalogs answer case-supplied constraints. */
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
    // Disabled reasons require visible explanation lines, never titles alone.
    expect(screen.getByText(/此 workflow 尺寸固定：宽高没有绑定到节点/)).toBeInTheDocument();
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
    // Bound frames without frame-rate sources look like fixed durations on project pages and also require explanations.
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
