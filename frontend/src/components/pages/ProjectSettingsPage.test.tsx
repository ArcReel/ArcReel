import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Router, Route } from "wouter";
import { memoryLocation } from "wouter/memory-location";
import "@/i18n";
import { API } from "@/api";
import * as providerModels from "@/utils/provider-models";
import { useAppStore } from "@/stores/app-store";
import { ProjectSettingsPage } from "@/components/pages/ProjectSettingsPage";

const FAKE_CONFIG = {
  options: { video_backends: [], image_backends: [], text_backends: [], provider_names: {} },
  settings: {
    default_video_backend: "",
    default_image_backend: "",
    text_backend_script: "",
    text_backend_overview: "",
    text_backend_style: "",
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
    text_backend_script: "gemini/g25",
    text_backend_overview: "gemini/g25",
    text_backend_style: "gemini/g25",
  },
};

function renderAt(path: string) {
  const location = memoryLocation({ path, record: true });
  return render(
    <Router hook={location.hook}>
      <Route path="/app/projects/:projectName/settings" component={ProjectSettingsPage} />
    </Router>,
  );
}

describe("ProjectSettingsPage – style picker", () => {
  beforeEach(() => {
    useAppStore.setState(useAppStore.getInitialState(), true);
    vi.restoreAllMocks();
    vi.spyOn(API, "getSystemConfig").mockResolvedValue(FAKE_CONFIG as unknown as Awaited<ReturnType<typeof API.getSystemConfig>>);
    vi.spyOn(providerModels, "getProviderModels").mockResolvedValue([]);
    vi.spyOn(providerModels, "getCustomProviderModels").mockResolvedValue([]);
  });

  it("loads a project with style_template_id and selects the matching template card by default", async () => {
    vi.spyOn(API, "getProject").mockResolvedValue({
      project: {
        title: "Demo",
        style_template_id: "live_zhang_yimou",
        style: "画风：参考张艺谋电影风格",
        episodes: [],
        characters: {},
        clues: {},
      },
      scripts: {},
    } as unknown as Awaited<ReturnType<typeof API.getProject>>);

    renderAt("/app/projects/demo/settings");

    await waitFor(() => {
      // Selected card has aria-pressed=true
      const selected = screen.getByRole("button", { name: /张艺谋/, pressed: true });
      expect(selected).toBeInTheDocument();
    });
  });

  it("loads a project with style_image and switches to custom tab with existing preview", async () => {
    vi.spyOn(API, "getProject").mockResolvedValue({
      project: {
        title: "Demo",
        style_image: "style_reference.png",
        style_description: "old desc",
        episodes: [],
        characters: {},
        clues: {},
      },
      scripts: {},
    } as unknown as Awaited<ReturnType<typeof API.getProject>>);

    renderAt("/app/projects/demo/settings");

    await waitFor(() => {
      const img = screen.getByAltText(/上传风格参考图|Upload style reference/) as HTMLImageElement;
      expect(img.src).toContain("/api/v1/files/demo/style_reference.png");
    });
  });

  it("clearing the reference image keeps save enabled and triggers clear PATCH", async () => {
    vi.spyOn(API, "getProject").mockResolvedValue({
      project: {
        title: "Demo",
        style_image: "style_reference.png",
        episodes: [],
        characters: {},
        clues: {},
      },
      scripts: {},
    } as unknown as Awaited<ReturnType<typeof API.getProject>>);
    const updateSpy = vi.spyOn(API, "updateProject").mockResolvedValue({
      success: true,
      project: { title: "Demo" } as unknown as Awaited<ReturnType<typeof API.updateProject>>["project"],
    });

    renderAt("/app/projects/demo/settings");

    await waitFor(() => screen.getByAltText(/上传风格参考图|Upload style reference/));
    const removeBtn = screen.getByRole("button", { name: /^remove$/i });
    fireEvent.click(removeBtn);

    // 移除自定义图后 save 应可点：保存即清除后端残留 style_image / description
    const saveBtn = screen.getByRole("button", { name: /保存风格|Save style/ });
    expect(saveBtn).not.toBeDisabled();
    fireEvent.click(saveBtn);

    await waitFor(() => {
      expect(updateSpy).toHaveBeenCalledWith("demo", {
        style_template_id: null,
        clear_style_image: true,
      });
    });
  });

  it("clicking 取消风格 when project has a template sends clear PATCH", async () => {
    vi.spyOn(API, "getProject").mockResolvedValue({
      project: {
        title: "Demo",
        style_template_id: "live_premium_drama",
        style: "画风：...",
        episodes: [],
        characters: {},
        clues: {},
      },
      scripts: {},
    } as unknown as Awaited<ReturnType<typeof API.getProject>>);
    const updateSpy = vi.spyOn(API, "updateProject").mockResolvedValue({
      success: true,
      project: { title: "Demo" } as unknown as Awaited<ReturnType<typeof API.updateProject>>["project"],
    });

    renderAt("/app/projects/demo/settings");

    // 等到 style picker 已经 mount（能找到保存按钮）
    await screen.findByRole("button", { name: /保存风格|Save style/ });

    const clearBtn = screen.getByRole("button", { name: /取消风格|Remove style/ });
    fireEvent.click(clearBtn);

    const saveBtn = screen.getByRole("button", { name: /保存风格|Save style/ });
    expect(saveBtn).not.toBeDisabled();
    fireEvent.click(saveBtn);

    await waitFor(() => {
      expect(updateSpy).toHaveBeenCalledWith("demo", {
        style_template_id: null,
        clear_style_image: true,
      });
    });
  });

  it("falls back to 9:16 aspect ratio highlight when project has no aspect_ratio set", async () => {
    vi.spyOn(API, "getProject").mockResolvedValue({
      project: {
        title: "Demo",
        episodes: [],
        characters: {},
        clues: {},
      },
      scripts: {},
    } as unknown as Awaited<ReturnType<typeof API.getProject>>);

    renderAt("/app/projects/demo/settings");

    const portrait = await screen.findByRole("radio", { name: /竖屏 9:16/ });
    expect(portrait).toBeChecked();
    const landscape = screen.getByRole("radio", { name: /横屏 16:9/ });
    expect(landscape).not.toBeChecked();
  });

  it("shows 'follow global default · provider · model' in model triggers when project has no model override", async () => {
    vi.spyOn(API, "getSystemConfig").mockResolvedValue(
      FAKE_CONFIG_WITH_DEFAULTS as unknown as Awaited<ReturnType<typeof API.getSystemConfig>>,
    );
    vi.spyOn(API, "getProject").mockResolvedValue({
      project: {
        title: "Demo",
        episodes: [],
        characters: {},
        clues: {},
      },
      scripts: {},
    } as unknown as Awaited<ReturnType<typeof API.getProject>>);

    renderAt("/app/projects/demo/settings");

    // Wait for config to load and a model trigger to render
    const imageTrigger = await screen.findByRole("combobox", { name: /图片模型/ });
    expect(imageTrigger).toHaveTextContent(/跟随全局默认/);
    expect(imageTrigger).toHaveTextContent(/nano-banana/);
  });

  it("saves a template change via PATCH style_template_id", async () => {
    vi.spyOn(API, "getProject").mockResolvedValue({
      project: {
        title: "Demo",
        style_template_id: "live_premium_drama",
        style: "...",
        episodes: [],
        characters: {},
        clues: {},
      },
      scripts: {},
    } as unknown as Awaited<ReturnType<typeof API.getProject>>);
    const updateSpy = vi.spyOn(API, "updateProject").mockResolvedValue({
      success: true,
      project: { title: "Demo", style_template_id: "live_zhang_yimou" } as unknown as Awaited<ReturnType<typeof API.updateProject>>["project"],
    });

    renderAt("/app/projects/demo/settings");

    const card = await screen.findByRole("button", { name: /张艺谋/ });
    fireEvent.click(card);

    const saveBtn = screen.getByRole("button", { name: /保存风格|Save style/ });
    expect(saveBtn).not.toBeDisabled();
    fireEvent.click(saveBtn);

    await waitFor(() => {
      expect(updateSpy).toHaveBeenCalledWith("demo", { style_template_id: "live_zhang_yimou" });
    });
  });
});
