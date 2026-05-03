import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import "@/i18n";
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
          duration_resolution_constraints: {},
        },
      },
    },
  ],
  customProviders: [],
  globalDefaults: {
    video: "gemini-aistudio/veo-3",
    imageT2I: "gemini-aistudio/nano-banana",
    imageI2I: "gemini-aistudio/nano-banana",
    textScript: "",
    textOverview: "",
    textStyle: "",
  },
} as unknown as WizardStep2Data;

const baseValue = {
  videoBackend: "",
  imageBackendT2I: "",
  imageBackendI2I: "",
  textBackendScript: "",
  textBackendOverview: "",
  textBackendStyle: "",
  defaultDuration: null,
  videoResolution: null,
  imageResolution: null,
};

describe("WizardStep2Models", () => {
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
    // 5 selectors — image 默认单下拉（仅当模型 caps 单一时才露出第 2 个）：
    // 1 video + 1 image + 3 text
    expect(screen.getAllByRole("combobox")).toHaveLength(5);
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
