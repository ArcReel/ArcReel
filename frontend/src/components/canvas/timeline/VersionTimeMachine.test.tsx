import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { API } from "@/api";
import { VersionTimeMachine } from "./VersionTimeMachine";
import { useAppStore } from "@/stores/app-store";

describe("VersionTimeMachine", () => {
  beforeEach(() => {
    useAppStore.setState(useAppStore.getInitialState(), true);
    vi.restoreAllMocks();
  });

  it("loads versions on demand and restores a previous version", async () => {
    vi.spyOn(API, "getVersions")
      .mockResolvedValueOnce({
        resource_type: "storyboards",
        resource_id: "SEG-1",
        current_version: 2,
        versions: [
          {
            version: 1,
            filename: "v1.png",
            created_at: "2026-02-01T00:00:00Z",
            file_size: 10,
            is_current: false,
            prompt: "old prompt",
            file_url: "/api/v1/files/demo/versions/storyboards/v1.png",
          },
          {
            version: 2,
            filename: "v2.png",
            created_at: "2026-02-01T01:00:00Z",
            file_size: 12,
            is_current: true,
            file_url: "/api/v1/files/demo/versions/storyboards/v2.png",
          },
        ],
      })
      .mockResolvedValueOnce({
        resource_type: "storyboards",
        resource_id: "SEG-1",
        current_version: 1,
        versions: [
          {
            version: 1,
            filename: "v1.png",
            created_at: "2026-02-01T00:00:00Z",
            file_size: 10,
            is_current: true,
            prompt: "old prompt",
            file_url: "/api/v1/files/demo/versions/storyboards/v1.png",
          },
          {
            version: 2,
            filename: "v2.png",
            created_at: "2026-02-01T01:00:00Z",
            file_size: 12,
            is_current: false,
          },
        ],
      });
    vi.spyOn(API, "restoreVersion").mockResolvedValue({ success: true });
    const onRestore = vi.fn().mockResolvedValue(undefined);

    render(
      <VersionTimeMachine
        projectName="demo"
        resourceType="storyboards"
        resourceId="SEG-1"
        onRestore={onRestore}
      />,
    );

    expect(API.getVersions).not.toHaveBeenCalled();

    // Open the panel
    fireEvent.click(screen.getByRole("button", { name: /版本/ }));

    // Click v1 pill to preview
    expect(await screen.findByRole("button", { name: "v1" })).toBeInTheDocument();
    expect(API.getVersions).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "v1" }));
    expect(await screen.findByAltText("版本 v1 预览")).toBeInTheDocument();
    expect(screen.getByText("old prompt")).toBeInTheDocument();

    // Click restore button in header
    fireEvent.click(screen.getByRole("button", { name: /切换到此版本/ }));

    await waitFor(() => {
      expect(API.restoreVersion).toHaveBeenCalledWith(
        "demo",
        "storyboards",
        "SEG-1",
        1,
      );
      expect(onRestore).toHaveBeenCalledWith(1);
      expect(API.getVersions).toHaveBeenCalledTimes(2);
      expect(useAppStore.getState().toast?.text).toBe("已切换到 v1");
    });
  });

  it("shows character preview with contain layout so tall images are not cropped", async () => {
    vi.spyOn(API, "getVersions").mockResolvedValue({
      resource_type: "characters",
      resource_id: "Hero",
      current_version: 2,
      versions: [
        {
          version: 1,
          filename: "v1.png",
          created_at: "2026-02-01T00:00:00Z",
          file_size: 10,
          is_current: false,
          prompt: "hero prompt",
          file_url: "/api/v1/files/demo/versions/characters/Hero_v1.png",
        },
        {
          version: 2,
          filename: "v2.png",
          created_at: "2026-02-01T01:00:00Z",
          file_size: 12,
          is_current: true,
          file_url: "/api/v1/files/demo/versions/characters/Hero_v2.png",
        },
      ],
    });

    render(
      <VersionTimeMachine
        projectName="demo"
        resourceType="characters"
        resourceId="Hero"
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /版本/ }));
    expect(await screen.findByRole("button", { name: "v1" })).toBeInTheDocument();

    // Click v1 pill to preview
    fireEvent.click(screen.getByRole("button", { name: "v1" }));

    const previewImage = await screen.findByAltText("版本 v1 预览");
    expect(previewImage).toHaveClass("object-contain");
    expect(previewImage.parentElement).toHaveClass("h-80");
  });

  it("previews and downloads historical narration audio without offering restore", async () => {
    vi.spyOn(API, "getVersions").mockResolvedValue({
      resource_type: "audio",
      resource_id: "E1S01",
      current_version: 2,
      versions: [
        {
          version: 1,
          filename: "E1S01_v1.wav",
          created_at: "2026-02-01T00:00:00Z",
          file_size: 10,
          is_current: false,
          file_url: "/api/v1/files/demo/versions/audio/E1S01_v1.wav",
        },
        {
          version: 2,
          filename: "E1S01_v2.wav",
          created_at: "2026-02-01T01:00:00Z",
          file_size: 12,
          is_current: true,
          file_url: "/api/v1/files/demo/versions/audio/E1S01_v2.wav",
        },
      ],
    });
    const restore = vi.spyOn(API, "restoreVersion");

    render(
      <VersionTimeMachine
        projectName="demo"
        resourceType="audio"
        resourceId="E1S01"
        readOnly
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /版本/ }));
    fireEvent.click(await screen.findByRole("button", { name: "v1" }));

    expect(await screen.findByLabelText("旁白音频版本 v1")).toHaveAttribute(
      "src",
      "/api/v1/files/demo/versions/audio/E1S01_v1.wav",
    );
    expect(screen.getByRole("link", { name: /下载音频/ })).toHaveAttribute("download");
    expect(screen.queryByRole("button", { name: /切换到此版本/ })).not.toBeInTheDocument();
    expect(restore).not.toHaveBeenCalled();
  });

  it("disables version restore while the resource is busy (image_edit in flight)", async () => {
    vi.spyOn(API, "getVersions").mockResolvedValue({
      resource_type: "storyboards",
      resource_id: "SEG-1",
      current_version: 2,
      versions: [
        {
          version: 1,
          filename: "v1.png",
          created_at: "2026-02-01T00:00:00Z",
          file_size: 10,
          is_current: false,
          prompt: "old prompt",
          file_url: "/api/v1/files/demo/versions/storyboards/v1.png",
        },
        {
          version: 2,
          filename: "v2.png",
          created_at: "2026-02-01T01:00:00Z",
          file_size: 12,
          is_current: true,
          file_url: "/api/v1/files/demo/versions/storyboards/v2.png",
        },
      ],
    });
    const restoreSpy = vi.spyOn(API, "restoreVersion").mockResolvedValue({ success: true });

    render(
      <VersionTimeMachine
        projectName="demo"
        resourceType="storyboards"
        resourceId="SEG-1"
        busy
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /版本/ }));
    expect(await screen.findByRole("button", { name: "v1" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "v1" }));

    const restoreButton = await screen.findByRole("button", { name: /切换到此版本/ });
    expect(restoreButton).toBeDisabled();
    expect(restoreButton).toHaveAttribute("title", "生成或编辑进行中，暂无法切换版本");

    fireEvent.click(restoreButton);
    expect(restoreSpy).not.toHaveBeenCalled();
  });

  // busy 是渲染快照：版本面板打开着的这段时间里 Agent 入队、批量入口或轮询落库都可能
  // 占用该资源，新 prop 冲刷到按钮之前的一次点击仍会发出恢复请求，与在跑的任务并发写
  // 同一个资源文件。故提交处理器里再做一次新鲜读。
  it("恢复提交时刻复核占用态，命中即拒绝并提示", async () => {
    vi.spyOn(API, "getVersions").mockResolvedValue({
      resource_type: "storyboards",
      resource_id: "SEG-1",
      current_version: 2,
      versions: [
        {
          version: 1,
          filename: "v1.png",
          created_at: "2026-02-01T00:00:00Z",
          file_size: 10,
          is_current: false,
          prompt: "old prompt",
          file_url: "/api/v1/files/demo/versions/storyboards/v1.png",
        },
        {
          version: 2,
          filename: "v2.png",
          created_at: "2026-02-01T01:00:00Z",
          file_size: 12,
          is_current: true,
          file_url: "/api/v1/files/demo/versions/storyboards/v2.png",
        },
      ],
    });
    const restoreSpy = vi.spyOn(API, "restoreVersion").mockResolvedValue({ success: true });
    // 渲染期空闲（busy 缺省为 false，按钮可点），提交时刻已被别的入口占用
    const checkBusy = vi.fn().mockReturnValue(true);

    render(
      <VersionTimeMachine
        projectName="demo"
        resourceType="storyboards"
        resourceId="SEG-1"
        checkBusy={checkBusy}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /版本/ }));
    expect(await screen.findByRole("button", { name: "v1" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "v1" }));

    const restoreButton = await screen.findByRole("button", { name: /切换到此版本/ });
    expect(restoreButton).not.toBeDisabled();

    fireEvent.click(restoreButton);

    expect(restoreSpy).not.toHaveBeenCalled();
    await waitFor(() =>
      expect(useAppStore.getState().toast?.text).toBe("生成或编辑进行中，暂无法切换版本"),
    );
  });
});
