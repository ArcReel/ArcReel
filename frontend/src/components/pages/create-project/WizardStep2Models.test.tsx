import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { beforeEach, describe, it, expect, vi } from "vitest";
import "@/i18n";
import { API } from "@/api";
import { WizardStep2Models, type WizardStep2Data } from "./WizardStep2Models";

const mockData = {
  options: {
    video: ["gemini-aistudio/veo-3"],
    image: ["gemini-aistudio/nano-banana"],
    text: ["gemini-aistudio/g25"],
    providerNames: { "gemini-aistudio": "Gemini AI Studio" },
  },
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
        },
      },
    },
  ],
  customProviders: [],
  globalDefaults: {
    video: "gemini-aistudio/veo-3",
    videoI2V: "",
    videoR2V: "",
    image: "gemini-aistudio/nano-banana",
    imageT2I: "",
    imageI2I: "",
    textDefault: "",
    textSimple: "",
    textComplex: "",
  },
} as unknown as WizardStep2Data;

const baseValue = {
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
};

describe("WizardStep2Models", () => {
  beforeEach(() => {
    vi.spyOn(API, "getModelVideoCapabilities").mockResolvedValue({
      provider_id: "gemini-aistudio",
      model: "veo-3",
      supported_durations: [4, 6, 8],
      max_duration: 8,
      max_reference_images: 3,
      first_frame: true,
      last_frame: true,
      source: "registry",
      voice_consistency: "soft",
      duration_constraints: {
        resolution: null,
        uses_reference_images: true,
        allowed: [8],
        allowed_without_reference_images: [4, 6, 8],
        excluded: { "4": "reference", "6": "reference" },
      },
    });
  });

  it("shows loading state when data is null and no error", () => {
    render(
      <WizardStep2Models
        value={baseValue}
        onChange={() => {}}
        onBack={() => {}}
        onNext={() => {}}
        onCancel={() => {}}
        data={null}
        error={null}
      />,
    );
    expect(screen.getByText(/loading|加载中/i)).toBeInTheDocument();
  });

  it("renders ModelConfigSection when data is provided", () => {
    render(
      <WizardStep2Models
        value={baseValue}
        onChange={() => {}}
        onBack={() => {}}
        onNext={() => {}}
        onCancel={() => {}}
        data={mockData}
        error={null}
      />,
    );
    expect(screen.queryByText(/loading|加载中/i)).not.toBeInTheDocument();
    // 向导只暴露默认层：video + image + text 三个主下拉，没有「按用途指定模型」折叠区
    expect(screen.getAllByRole("combobox")).toHaveLength(3);
    expect(screen.queryByText("按用途指定模型")).not.toBeInTheDocument();
  });

  it("asks the no-project endpoint for the wizard's candidate model and renders its narrowed durations", async () => {
    render(
      <WizardStep2Models
        value={baseValue}
        onChange={() => {}}
        onBack={() => {}}
        onNext={() => {}}
        onCancel={() => {}}
        data={mockData}
        error={null}
        usesReferenceImages
      />,
    );
    // 项目尚不存在：不查项目端点，按全局默认解析出的候选模型走无项目端点，并带上参考图路径
    await waitFor(() =>
      expect(API.getModelVideoCapabilities).toHaveBeenCalledWith(
        "gemini-aistudio/veo-3",
        expect.objectContaining({ usesReferenceImages: true, resolution: null }),
      ),
    );
    expect(await screen.findByRole("radio", { name: "8 秒" })).toBeInTheDocument();
    expect(screen.queryByRole("radio", { name: "4 秒" })).not.toBeInTheDocument();
  });

  it("calls onBack when previous button is clicked", () => {
    const onBack = vi.fn();
    render(
      <WizardStep2Models
        value={baseValue}
        onChange={() => {}}
        onBack={onBack}
        onNext={() => {}}
        onCancel={() => {}}
        data={mockData}
        error={null}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /上一步|Back/i }));
    expect(onBack).toHaveBeenCalledOnce();
  });

  it("calls onNext when next button is clicked", () => {
    const onNext = vi.fn();
    render(
      <WizardStep2Models
        value={baseValue}
        onChange={() => {}}
        onBack={() => {}}
        onNext={onNext}
        onCancel={() => {}}
        data={mockData}
        error={null}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /下一步|Next/i }));
    expect(onNext).toHaveBeenCalledOnce();
  });

  it("calls onCancel when cancel button is clicked", () => {
    const onCancel = vi.fn();
    render(
      <WizardStep2Models
        value={baseValue}
        onChange={() => {}}
        onBack={() => {}}
        onNext={() => {}}
        onCancel={onCancel}
        data={mockData}
        error={null}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /取消|Cancel/i }));
    expect(onCancel).toHaveBeenCalledOnce();
  });

  it("shows error message when error prop is passed", () => {
    render(
      <WizardStep2Models
        value={baseValue}
        onChange={() => {}}
        onBack={() => {}}
        onNext={() => {}}
        onCancel={() => {}}
        data={null}
        error="network down"
      />,
    );
    expect(screen.getByText(/network down/)).toBeInTheDocument();
  });
});
