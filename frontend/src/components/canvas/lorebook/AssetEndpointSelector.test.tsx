import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { API } from "@/api";
import { refreshAfterWrite } from "@/components/canvas/shared/refreshAfterWrite";
import { useAssetImageEndpoints } from "@/hooks/useAssetImageEndpoints";
import { useAppStore } from "@/stores/app-store";
import type { ResolvedAssetImageEndpoint } from "@/utils/asset-image-endpoint";
import { AssetEndpointSelector } from "./AssetEndpointSelector";

vi.mock("@/hooks/useAssetImageEndpoints", () => ({
  useAssetImageEndpoints: vi.fn(),
}));
vi.mock("@/components/canvas/shared/refreshAfterWrite", () => ({
  refreshAfterWrite: vi.fn().mockResolvedValue(undefined),
}));

function stubEndpoints(current: ResolvedAssetImageEndpoint) {
  vi.mocked(useAssetImageEndpoints).mockReturnValue({
    options: ["openai/gpt-image-1", "gemini/nano-banana"],
    providerNames: { openai: "OpenAI", gemini: "AI Studio" },
    modelNames: { "openai/gpt-image-1": "GPT Image 1", "gemini/nano-banana": "Nano Banana" },
    current,
    loading: false,
    error: false,
    reload: vi.fn(),
  });
}

function renderSelector(props: Partial<Parameters<typeof AssetEndpointSelector>[0]> = {}) {
  const onChange = vi.fn();
  const utils = render(
    <AssetEndpointSelector
      projectName="demo"
      assetType="scene"
      useImageToImage={false}
      value=""
      onChange={onChange}
      busy={false}
      {...props}
    />,
  );
  return { onChange, ...utils };
}

describe("AssetEndpointSelector", () => {
  beforeEach(() => {
    useAppStore.setState(useAppStore.getInitialState(), true);
    vi.spyOn(API, "updateProject").mockResolvedValue({ success: true } as never);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("shows the current effective endpoint when nothing is overridden", () => {
    stubEndpoints({ value: "gemini/nano-banana", source: "global_default" });
    renderSelector();
    // 空覆盖时触发按钮回退到当前生效端点，来源层显示为「全局默认」
    expect(screen.getByRole("combobox", { name: "生成端点" })).toHaveTextContent("Nano Banana");
    expect(screen.getByText(/当前：全局默认/)).toBeInTheDocument();
  });

  it("reads the source caption from the project layer", () => {
    stubEndpoints({ value: "openai/gpt-image-1", source: "project_slot" });
    renderSelector();
    expect(screen.getByText(/当前：项目默认/)).toBeInTheDocument();
  });

  it("reports the auto-inferred source when no layer is configured", () => {
    stubEndpoints({ value: "", source: "auto" });
    renderSelector();
    expect(screen.getByText(/当前：自动/)).toBeInTheDocument();
  });

  it("forwards the picked endpoint to onChange", async () => {
    const user = userEvent.setup();
    stubEndpoints({ value: "gemini/nano-banana", source: "global_default" });
    const { onChange } = renderSelector();
    await user.click(screen.getByRole("combobox", { name: "生成端点" }));
    await screen.findByRole("listbox");
    await user.click(await screen.findByRole("option", { name: /GPT Image 1/ }));
    expect(onChange).toHaveBeenCalledWith("openai/gpt-image-1");
  });

  it("restores the follow-default option to an empty override", async () => {
    const user = userEvent.setup();
    stubEndpoints({ value: "gemini/nano-banana", source: "global_default" });
    const { onChange } = renderSelector({ value: "gemini/nano-banana" });
    await user.click(screen.getByRole("combobox", { name: "生成端点" }));
    await screen.findByRole("listbox");
    await user.click(await screen.findByRole("option", { name: /跟随默认/ }));
    expect(onChange).toHaveBeenCalledWith("");
  });

  it("writes the project asset slot and clears the override on set default", async () => {
    const user = userEvent.setup();
    stubEndpoints({ value: "gemini/nano-banana", source: "global_default" });
    const { onChange } = renderSelector();
    await user.click(screen.getByRole("button", { name: "设为默认" }));
    await waitFor(() =>
      expect(API.updateProject).toHaveBeenCalledWith("demo", { default_image_backend_scene: "gemini/nano-banana" }),
    );
    expect(refreshAfterWrite).toHaveBeenCalledWith("demo", expect.anything());
    expect(onChange).toHaveBeenCalledWith("");
    expect(useAppStore.getState().toast?.tone).toBe("success");
  });

  it("uses the selected override, not the current value, when setting default", async () => {
    const user = userEvent.setup();
    stubEndpoints({ value: "gemini/nano-banana", source: "global_default" });
    renderSelector({ value: "openai/gpt-image-1" });
    await user.click(screen.getByRole("button", { name: "设为默认" }));
    await waitFor(() =>
      expect(API.updateProject).toHaveBeenCalledWith("demo", { default_image_backend_scene: "openai/gpt-image-1" }),
    );
  });

  it("disables set default when no endpoint can be resolved", () => {
    stubEndpoints({ value: "", source: "auto" });
    renderSelector();
    expect(screen.getByRole("button", { name: "设为默认" })).toBeDisabled();
  });
});
