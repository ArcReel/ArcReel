import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Router } from "wouter";
import { memoryLocation } from "wouter/memory-location";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { useConfigStatusStore } from "@/stores/config-status-store";
import { SystemConfigPage } from "@/components/pages/SystemConfigPage";
import type { GetSystemConfigResponse } from "@/types";

function makeConfigResponse(): GetSystemConfigResponse {
  return {
    config: {
      image_backend: "aistudio" as const,
      video_backend: "vertex" as const,
      image_model: "gemini-3.1-flash-image-preview",
      video_model: "veo-3.1-generate-001",
      video_generate_audio: true,
      video_generate_audio_effective: true,
      video_generate_audio_editable: true,
      rate_limit: {
        image_rpm: 15,
        video_rpm: 10,
        request_gap_seconds: 3.1,
      },
      performance: {
        image_max_workers: 3,
        video_max_workers: 2,
      },
      gemini_api_key: {
        is_set: false,
        masked: null,
        source: "unset" as const,
      },
      gemini_base_url: {
        value: null,
        source: "unset" as const,
      },
      anthropic_api_key: {
        is_set: false,
        masked: null,
        source: "unset" as const,
      },
      anthropic_base_url: {
        value: null,
        source: "unset" as const,
      },
      anthropic_model: {
        value: null,
        source: "unset" as const,
      },
      anthropic_default_haiku_model: {
        value: null,
        source: "unset" as const,
      },
      anthropic_default_opus_model: {
        value: null,
        source: "unset" as const,
      },
      anthropic_default_sonnet_model: {
        value: null,
        source: "unset" as const,
      },
      claude_code_subagent_model: {
        value: null,
        source: "unset" as const,
      },
      vertex_gcs_bucket: {
        value: null,
        source: "unset" as const,
      },
      vertex_credentials: {
        is_set: true,
        filename: "vertex_credentials.json",
        project_id: "demo-project",
      },
    },
    options: {
      image_models: ["gemini-3.1-flash-image-preview"],
      video_models: ["veo-3.1-generate-001"],
    },
  };
}

function renderPage() {
  const location = memoryLocation({ path: "/app/settings", record: true });
  return render(
    <Router hook={location.hook}>
      <SystemConfigPage />
    </Router>,
  );
}

describe("SystemConfigPage", () => {
  beforeEach(() => {
    useAppStore.setState(useAppStore.getInitialState(), true);
    useConfigStatusStore.setState(useConfigStatusStore.getInitialState(), true);
    vi.restoreAllMocks();
  });

  it("shows an error state and allows retry when the initial load fails", async () => {
    vi.spyOn(API, "getSystemConfig")
      .mockRejectedValueOnce(new Error("network down"))
      .mockResolvedValue(makeConfigResponse());

    renderPage();

    expect(await screen.findByText("配置加载失败")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "重试加载" }));

    expect(await screen.findByText("Anthropic API Key")).toBeInTheDocument();
  });

  it("tests vertex connection from the page", async () => {
    vi.spyOn(API, "getSystemConfig").mockResolvedValue(makeConfigResponse());
    vi.spyOn(API, "testSystemConnection").mockResolvedValue({
      ok: true,
      provider: "vertex",
      filename: "vertex_credentials.json",
      project_id: "demo-project",
      checked_models: [{ media_type: "video", model: "veo-3.1-generate-001" }],
      missing_models: [],
      message: "Vertex 可用，models.list 已返回目标模型：video:veo-3.1-generate-001",
    });

    renderPage();

    expect(await screen.findByText("Vertex AI 凭证")).toBeInTheDocument();
    const testButtons = screen.getAllByRole("button", { name: "测试连接" });
    fireEvent.click(testButtons[1]);

    await waitFor(() => {
      expect(API.testSystemConnection).toHaveBeenCalledWith({
        provider: "vertex",
        image_backend: "aistudio",
        video_backend: "vertex",
        image_model: "gemini-3.1-flash-image-preview",
        video_model: "veo-3.1-generate-001",
        gemini_api_key: null,
      });
    });
    expect(await screen.findByText(/models\.list 已返回目标模型/)).toBeInTheDocument();
  });

  it("tests ai studio using the input key override", async () => {
    vi.spyOn(API, "getSystemConfig").mockResolvedValue(makeConfigResponse());
    vi.spyOn(API, "testSystemConnection").mockResolvedValue({
      ok: true,
      provider: "aistudio",
      filename: null,
      project_id: null,
      checked_models: [{ media_type: "image", model: "gemini-3.1-flash-image-preview" }],
      missing_models: [],
      message: "AI Studio 可用，models.list 已返回目标模型：image:gemini-3.1-flash-image-preview",
    });

    renderPage();

    expect(await screen.findByText("Gemini API Key")).toBeInTheDocument();
    fireEvent.change(
      screen.getByPlaceholderText("AIza…"),
      { target: { value: "AIza-override" } },
    );
    const testButtons = screen.getAllByRole("button", { name: "测试连接" });
    fireEvent.click(testButtons[0]);

    await waitFor(() => {
      expect(API.testSystemConnection).toHaveBeenCalledWith({
        provider: "aistudio",
        image_backend: "aistudio",
        video_backend: "vertex",
        image_model: "gemini-3.1-flash-image-preview",
        video_model: "veo-3.1-generate-001",
        gemini_api_key: "AIza-override",
      });
    });
    expect(await screen.findByText(/models\.list 已返回目标模型/)).toBeInTheDocument();
  });

  it("hides override source labels and advanced config helper copy", async () => {
    const response = makeConfigResponse();
    response.config.gemini_api_key = {
      is_set: true,
      masked: "AIza...1234",
      source: "override" as const,
    };
    response.config.anthropic_api_key = {
      is_set: true,
      masked: "sk-ant...5678",
      source: "override" as const,
    };

    vi.spyOn(API, "getSystemConfig").mockResolvedValue(response);

    renderPage();

    expect(await screen.findByText("Anthropic API Key")).toBeInTheDocument();
    expect(screen.getByText(/当前：sk-ant\.\.\.5678/)).toBeInTheDocument();
    expect(screen.getByText(/当前：AIza\.\.\.1234/)).toBeInTheDocument();
    expect(screen.queryByText("UI 覆盖")).not.toBeInTheDocument();
    expect(screen.queryByText("STORYBOARD/VIDEO workers")).not.toBeInTheDocument();
  });

  it("saves anthropic base url from the top key card", async () => {
    const updated = makeConfigResponse();
    updated.config.anthropic_base_url = {
      value: "https://proxy.example.com/v1",
      source: "override",
    };

    vi.spyOn(API, "getSystemConfig").mockResolvedValue(makeConfigResponse());
    vi.spyOn(API, "updateSystemConfig").mockResolvedValue(updated);

    renderPage();

    const anthropicHeading = await screen.findByText("Anthropic API Key");
    const geminiHeading = screen.getByText("Gemini API Key");
    expect(
      Boolean(anthropicHeading.compareDocumentPosition(geminiHeading) & Node.DOCUMENT_POSITION_FOLLOWING),
    ).toBe(true);
    expect(
      screen
        .getAllByRole("button", { name: "保存" })
        .every((button) => (button as HTMLButtonElement).disabled),
    ).toBe(true);

    fireEvent.change(screen.getByPlaceholderText("https://anthropic-proxy.example.com"), {
      target: { value: "https://proxy.example.com/v1" },
    });
    expect(API.updateSystemConfig).not.toHaveBeenCalled();
    const saveButton = screen
      .getAllByRole("button", { name: "保存" })
      .find((button) => !(button as HTMLButtonElement).disabled);
    expect(saveButton).toBeDefined();
    fireEvent.click(saveButton as HTMLButtonElement);

    await waitFor(() => {
      expect(API.updateSystemConfig).toHaveBeenCalledWith({
        anthropic_base_url: "https://proxy.example.com/v1",
      });
    });
    expect(screen.getByDisplayValue("https://proxy.example.com/v1")).toBeInTheDocument();
  });
});
