import type { ComponentProps } from "react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, onTestFinished, vi } from "vitest";
import { makeNarrationSegment } from "@/test/factories";
import userEvent from "@testing-library/user-event";
import { createDeferred } from "@/test/deferred";
import { API } from "@/api";
import { VersionTimeMachine } from "./VersionTimeMachine";
import { useProjectsStore } from "@/stores/projects-store";
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
    });
    // 切换结果体现在「当前」标记上，成功不弹提示
    expect(await screen.findByText("当前 v1")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /切换到此版本/ })).not.toBeInTheDocument();
    expect(useAppStore.getState().toast).toBeNull();
  });

  it("reloads product history when the product image fingerprint changes", async () => {
    useProjectsStore.setState({ assetFingerprints: { "products/Cup.png": 1 } });
    vi.spyOn(API, "getVersions")
      .mockResolvedValueOnce({ resource_type: "products", resource_id: "Cup", current_version: 1, versions: [] })
      .mockResolvedValueOnce({ resource_type: "products", resource_id: "Cup", current_version: 2, versions: [] });
    render(<VersionTimeMachine projectName="demo" resourceType="products" resourceId="Cup" open />);
    expect(await screen.findByText("当前 v1")).toBeInTheDocument();

    act(() => useProjectsStore.getState().updateAssetFingerprints({ "products/Cup.png": 2 }));

    expect(await screen.findByText("当前 v2")).toBeInTheDocument();
    useProjectsStore.setState({ assetFingerprints: {} });
  });

  it("drops the previewed version when the resource gets a new version elsewhere, but keeps the one just restored", async () => {
    useProjectsStore.setState({ assetFingerprints: { "products/Cup.png": 1 } });
    const versions = [
      { version: 1, filename: "v1.png", created_at: "2026-10-01", file_size: 1, is_current: false, file_url: "/v1.png" },
      { version: 2, filename: "v2.png", created_at: "2026-10-02", file_size: 1, is_current: true, file_url: "/v2.png" },
    ];
    vi.spyOn(API, "getVersions").mockResolvedValue({
      resource_type: "products", resource_id: "Cup", current_version: 2, versions,
    });
    vi.spyOn(API, "restoreVersion").mockResolvedValue({
      success: true,
      asset_fingerprints: { "products/Cup.png": 3 },
    });
    render(<VersionTimeMachine projectName="demo" resourceType="products" resourceId="Cup" open />);

    fireEvent.click(await screen.findByRole("button", { name: "v1" }));
    expect(screen.getByAltText("版本 v1 预览")).toBeInTheDocument();
    // 别处（如 Agent）生成了新版本：预览选中不沿用到新的版本列表
    act(() => useProjectsStore.getState().updateAssetFingerprints({ "products/Cup.png": 2 }));
    expect(screen.queryByAltText("版本 v1 预览")).not.toBeInTheDocument();

    fireEvent.click(await screen.findByRole("button", { name: "v1" }));
    fireEvent.click(screen.getByRole("button", { name: "切换到此版本" }));
    await waitFor(() => expect(API.restoreVersion).toHaveBeenCalledWith("demo", "products", "Cup", 1));
    // 恢复本身换了指纹，刚恢复的版本仍保持选中
    await waitFor(() => expect(useProjectsStore.getState().assetFingerprints["products/Cup.png"]).toBe(3));
    expect(await screen.findByAltText("版本 v1 预览")).toBeInTheDocument();
    useProjectsStore.setState({ assetFingerprints: {} });
  });

  it("ignores Escape while a version restore is in flight", async () => {
    const user = userEvent.setup();
    const pending = createDeferred<{ success: boolean }>();
    vi.spyOn(API, "getVersions").mockResolvedValue({
      resource_type: "products", resource_id: "Cup", current_version: 2,
      versions: [{ version: 1, filename: "v1.png", created_at: "2026-10-01", file_size: 1, is_current: false }],
    });
    vi.spyOn(API, "restoreVersion").mockReturnValue(pending.promise);
    render(<VersionTimeMachine projectName="demo" resourceType="products" resourceId="Cup" />);
    await user.click(screen.getByRole("button", { name: /版本/ }));
    await user.click(await screen.findByRole("button", { name: "v1" }));
    await user.click(screen.getByRole("button", { name: "切换到此版本" }));
    await user.keyboard("{Escape}");
    expect(screen.getByText("历史版本")).toBeInTheDocument();
    await act(() => pending.resolve({ success: true }));
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByText("历史版本")).not.toBeInTheDocument());
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

  it("previews, downloads, and restores verified historical narration audio", async () => {
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
          restorable: true,
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
    const restore = vi.spyOn(API, "restoreVersion").mockResolvedValue({ success: true });

    render(
      <VersionTimeMachine
        projectName="demo"
        resourceType="audio"
        resourceId="E1S01"
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /版本/ }));
    fireEvent.click(await screen.findByRole("button", { name: "v1" }));

    expect(await screen.findByLabelText("旁白配音版本 v1")).toHaveAttribute(
      "src",
      "/api/v1/files/demo/versions/audio/E1S01_v1.wav",
    );
    expect(screen.getByRole("link", { name: /下载音频/ })).toHaveAttribute("download");
    fireEvent.click(screen.getByRole("button", { name: /切换到此版本/ }));
    await waitFor(() => {
      expect(restore).toHaveBeenCalledWith("demo", "audio", "E1S01", 1);
    });
  });

  it("does not offer restore for history-only narration audio", async () => {
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
          restorable: false,
          file_url: "/api/v1/files/demo/versions/audio/E1S01_v1.wav",
        },
        {
          version: 2,
          filename: "E1S01_v2.wav",
          created_at: "2026-02-01T01:00:00Z",
          file_size: 12,
          is_current: true,
          restorable: true,
          file_url: "/api/v1/files/demo/versions/audio/E1S01_v2.wav",
        },
      ],
    });
    const restore = vi.spyOn(API, "restoreVersion").mockResolvedValue({ success: true });

    render(
      <VersionTimeMachine
        projectName="demo"
        resourceType="audio"
        resourceId="E1S01"
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /版本/ }));
    fireEvent.click(await screen.findByRole("button", { name: "v1" }));

    expect(await screen.findByLabelText("旁白配音版本 v1")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /切换到此版本/ })).not.toBeInTheDocument();
    expect(restore).not.toHaveBeenCalled();
  });

  it("previews unverifiable legacy video history as raw media without materializing a presentation", async () => {
    vi.spyOn(API, "getVersions").mockResolvedValue({
      resource_type: "videos",
      resource_id: "E1S01",
      current_version: 2,
      versions: [
        {
          version: 1,
          filename: "E1S01_v1.mp4",
          created_at: "2026-02-01T00:00:00Z",
          file_size: 10,
          is_current: false,
          restorable: false,
          presentation_available: false,
          file_url: "/api/v1/files/demo/versions/videos/E1S01_v1.mp4",
        },
        {
          version: 2,
          filename: "E1S01_v2.mp4",
          created_at: "2026-02-01T01:00:00Z",
          file_size: 12,
          is_current: true,
          restorable: true,
          file_url: "/api/v1/files/demo/versions/videos/E1S01_v2.mp4",
        },
      ],
    });
    const presentation = vi.spyOn(API, "getPresentation");

    render(
      <VersionTimeMachine
        projectName="demo"
        resourceType="videos"
        resourceId="E1S01"
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /版本/ }));
    fireEvent.click(await screen.findByRole("button", { name: "v1" }));

    expect(await screen.findByLabelText("版本 v1 预览")).toHaveAttribute(
      "src",
      "/api/v1/files/demo/versions/videos/E1S01_v1.mp4",
    );
    expect(screen.queryByRole("button", { name: /切换到此版本/ })).not.toBeInTheDocument();
    expect(presentation).not.toHaveBeenCalled();
  });

  it("previews manual-upload history through the shared raw presentation seam", async () => {
    vi.spyOn(API, "getVersions").mockResolvedValue({
      resource_type: "videos",
      resource_id: "E1S01",
      current_version: 2,
      versions: [
        {
          version: 1,
          filename: "E1S01_v1.mp4",
          created_at: "2026-02-01T00:00:00Z",
          file_size: 10,
          is_current: false,
          restorable: false,
          presentation_available: true,
          source: "manual_upload",
          file_url: "/api/v1/files/demo/versions/videos/E1S01_v1.mp4",
        },
      ],
    });
    const presentation = vi.spyOn(API, "getPresentation").mockResolvedValue({
      schema_version: 1,
      provenance: "unavailable",
      episode: 1,
      resource_type: "videos",
      script_file: "episode_1.json",
      subtitle_artifact_path: null,
      presentation_artifact_path: null,
      persisted: false,
      unit_id: "E1S01",
      variant: "post_production",
      speech_mode: null,
      selection: "history",
      currency: null,
      video: {
        artifact_path: "versions/videos/E1S01_v1.mp4",
        version: 1,
        selection: "history",
        currency: null,
        basis: null,
        content_digest: `sha256-v1:${"a".repeat(64)}`,
        actual_duration_seconds: 4,
        start_microseconds: 0,
        duration_microseconds: 4_000_000,
        audio_enabled: true,
        gain: 1,
      },
      narration_audio: null,
      subtitles: [],
      subtitle_basis: null,
      presentation_basis: null,
      timing: null,
      subtitles_adjustable: false,
      subtitles_webvtt: null,
    });

    // 预览的可访问名称按本集位置称呼分镜：E1S01 排在第 2 位
    useProjectsStore.setState({
      currentScripts: {
        "episode_1.json": {
          episode: 1,
          content_mode: "narration",
          segments: [makeNarrationSegment({ segment_id: "E1S00" }), makeNarrationSegment({ segment_id: "E1S01" })],
        } as never,
      },
    });
    onTestFinished(() => useProjectsStore.setState({ currentScripts: {} }));
    render(
      <VersionTimeMachine projectName="demo" resourceType="videos" resourceId="E1S01" />,
    );
    fireEvent.click(screen.getByRole("button", { name: /版本/ }));
    fireEvent.click(await screen.findByRole("button", { name: "v1" }));

    const previewSource = (await screen.findByLabelText("分镜 2 成片预览")).getAttribute("src");
    expect(previewSource?.startsWith("/api/v1/files/demo/versions/videos/E1S01_v1.mp4")).toBe(true);
    expect(presentation).toHaveBeenCalledWith(
      "demo",
      "videos",
      "E1S01",
      expect.objectContaining({ videoVersion: 1 }),
    );
    expect(screen.getByText("来源不可用")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /切换到此版本/ })).not.toBeInTheDocument();
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
    expect(restoreButton).toHaveAccessibleDescription("生成或编辑进行中，暂无法切换版本");

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
    expect(restoreButton).toBeEnabled();

    fireEvent.click(restoreButton);

    expect(restoreSpy).not.toHaveBeenCalled();
    await waitFor(() =>
      expect(useAppStore.getState().toast?.text).toBe("生成或编辑进行中，暂无法切换版本"),
    );
  });

  describe("moving a history version to the project trash", () => {
    const deleted = { record: { id: "rec-1" } } as Awaited<ReturnType<typeof API.deleteFileViewEntry>>;

    function mockVersions() {
      return vi.spyOn(API, "getVersions").mockResolvedValue({
        resource_type: "storyboards",
        resource_id: "SEG-1",
        current_version: 2,
        versions: [
          { version: 1, filename: "v1.png", file: "versions/storyboards/SEG-1_v1.png", created_at: "2026-02-01", file_size: 1, is_current: false, file_url: "/v1.png" },
          { version: 2, filename: "v2.png", file: "versions/storyboards/SEG-1_v2.png", created_at: "2026-02-02", file_size: 1, is_current: true, file_url: "/v2.png" },
          { version: 3, filename: "v3.png", file: "versions/storyboards/SEG-1_v3.png", created_at: "2026-02-03", file_size: 1, is_current: false, file_url: "/v3.png" },
        ],
      });
    }

    function renderOpen(props: Partial<ComponentProps<typeof VersionTimeMachine>> = {}) {
      render(<VersionTimeMachine projectName="demo" resourceType="storyboards" resourceId="SEG-1" open {...props} />);
    }

    it("moves a version to the trash only after a second click, and the toast restores it", async () => {
      const list = mockVersions();
      const remove = vi.spyOn(API, "deleteFileViewEntry").mockResolvedValue(deleted);
      const restore = vi.spyOn(API, "restoreTrashRecord").mockResolvedValue({} as Awaited<ReturnType<typeof API.restoreTrashRecord>>);
      renderOpen();

      fireEvent.click(await screen.findByRole("button", { name: "将 v1 移入项目回收站" }));
      expect(remove).not.toHaveBeenCalled();
      const confirm = screen.getByRole("button", { name: "v1：移入项目回收站？" });
      expect(confirm).toHaveFocus();
      expect(confirm).toHaveTextContent("移入项目回收站？");

      fireEvent.click(confirm);
      await waitFor(() => expect(remove).toHaveBeenCalledWith("demo", "versions/storyboards/SEG-1_v1.png"));
      await waitFor(() => expect(screen.queryByRole("button", { name: "v1" })).not.toBeInTheDocument());
      expect(screen.getByRole("button", { name: "v2" })).toHaveFocus();

      const toast = useAppStore.getState().toast;
      expect(toast?.text).toBe("v1 已移入项目回收站，保留 30 天");
      expect(toast?.action?.label).toBe("恢复");
      const revisionBefore = useAppStore.getState().trashRevision;
      list.mockClear();
      act(() => toast?.action?.onClick());
      await waitFor(() => expect(restore).toHaveBeenCalledWith("demo", "rec-1"));
      await waitFor(() => expect(list).toHaveBeenCalledTimes(1));
      await waitFor(() => expect(useAppStore.getState().trashRevision).toBeGreaterThan(revisionBefore));
    });

    it("does not treat the second click of a double-click as confirmation", async () => {
      mockVersions();
      const remove = vi.spyOn(API, "deleteFileViewEntry").mockResolvedValue(deleted);
      renderOpen();
      fireEvent.click(await screen.findByRole("button", { name: "将 v1 移入项目回收站" }));
      fireEvent.click(screen.getByRole("button", { name: "v1：移入项目回收站？" }), { detail: 2 });
      expect(remove).not.toHaveBeenCalled();
      expect(screen.getByRole("button", { name: "v1：移入项目回收站？" })).toBeInTheDocument();
    });

    it("cancels with Escape, returning focus to the icon, without closing the panel", async () => {
      const user = userEvent.setup();
      mockVersions();
      const remove = vi.spyOn(API, "deleteFileViewEntry").mockResolvedValue(deleted);
      renderOpen();
      await user.click(await screen.findByRole("button", { name: "将 v1 移入项目回收站" }));
      await user.keyboard("{Escape}");
      expect(screen.getByRole("button", { name: "将 v1 移入项目回收站" })).toHaveFocus();
      expect(screen.getByText("历史版本")).toBeInTheDocument();
      expect(remove).not.toHaveBeenCalled();
    });

    it("can be confirmed from the keyboard", async () => {
      const user = userEvent.setup();
      mockVersions();
      const remove = vi.spyOn(API, "deleteFileViewEntry").mockResolvedValue(deleted);
      renderOpen();
      screen.getByText("历史版本");
      (await screen.findByRole("button", { name: "将 v1 移入项目回收站" })).focus();
      await user.keyboard("{Enter}");
      expect(screen.getByRole("button", { name: "v1：移入项目回收站？" })).toHaveFocus();
      await user.keyboard("{Enter}");
      await waitFor(() => expect(remove).toHaveBeenCalledTimes(1));
    });

    it("cancels when focus moves elsewhere", async () => {
      const user = userEvent.setup();
      mockVersions();
      const remove = vi.spyOn(API, "deleteFileViewEntry").mockResolvedValue(deleted);
      renderOpen();
      await user.click(await screen.findByRole("button", { name: "将 v1 移入项目回收站" }));
      await user.click(screen.getByRole("button", { name: "v3" }));
      expect(screen.queryByRole("button", { name: "v1：移入项目回收站？" })).not.toBeInTheDocument();
      expect(screen.getByRole("button", { name: "将 v1 移入项目回收站" })).toBeInTheDocument();
      expect(remove).not.toHaveBeenCalled();
    });

    it("greys out the current version with the reason and never deletes it", async () => {
      mockVersions();
      const remove = vi.spyOn(API, "deleteFileViewEntry").mockResolvedValue(deleted);
      renderOpen();
      const current = await screen.findByRole("button", { name: "当前版本不能删除，先切换到其他版本" });
      expect(current).toHaveAttribute("aria-disabled", "true");
      fireEvent.click(current);
      expect(screen.queryByRole("button", { name: /v2：/ })).not.toBeInTheDocument();
      expect(remove).not.toHaveBeenCalled();
    });

    it("returns the preview to the current version after deleting the previewed one", async () => {
      mockVersions();
      vi.spyOn(API, "deleteFileViewEntry").mockResolvedValue(deleted);
      renderOpen();
      fireEvent.click(await screen.findByRole("button", { name: "v1" }));
      expect(screen.getByAltText("版本 v1 预览")).toBeInTheDocument();

      fireEvent.click(screen.getByRole("button", { name: "将 v1 移入项目回收站" }));
      fireEvent.click(screen.getByRole("button", { name: "v1：移入项目回收站？" }));

      expect(await screen.findByAltText("版本 v2 预览")).toBeInTheDocument();
      expect(screen.queryByAltText("版本 v1 预览")).not.toBeInTheDocument();
    });

    it("leaves the preview alone when another version is deleted", async () => {
      mockVersions();
      vi.spyOn(API, "deleteFileViewEntry").mockResolvedValue(deleted);
      renderOpen();
      fireEvent.click(await screen.findByRole("button", { name: "v3" }));
      fireEvent.click(screen.getByRole("button", { name: "将 v1 移入项目回收站" }));
      fireEvent.click(screen.getByRole("button", { name: "v1：移入项目回收站？" }));
      await waitFor(() => expect(screen.queryByRole("button", { name: "v1" })).not.toBeInTheDocument());
      expect(screen.getByAltText("版本 v3 预览")).toBeInTheDocument();
    });

    it("keeps the version and reports the error when deletion fails", async () => {
      const list = mockVersions();
      vi.spyOn(API, "deleteFileViewEntry").mockRejectedValue(new Error("boom"));
      renderOpen();
      fireEvent.click(await screen.findByRole("button", { name: "将 v1 移入项目回收站" }));
      list.mockClear();
      fireEvent.click(screen.getByRole("button", { name: "v1：移入项目回收站？" }));
      await waitFor(() => expect(useAppStore.getState().toast?.text).toBe("无法删除 v1：boom"));
      // 列表可能已过期（别处切换了当前版本）：重拉，但不闪成加载态、焦点仍在图标上
      await waitFor(() => expect(list).toHaveBeenCalledTimes(1));
      expect(screen.getByRole("button", { name: "v1" })).toBeInTheDocument();
      await waitFor(() => expect(screen.getByRole("button", { name: "将 v1 移入项目回收站" })).toHaveFocus());
    });

    it("ignores Escape while the deletion is in flight", async () => {
      const user = userEvent.setup();
      const pending = createDeferred<typeof deleted>();
      mockVersions();
      vi.spyOn(API, "deleteFileViewEntry").mockReturnValue(pending.promise);
      render(<VersionTimeMachine projectName="demo" resourceType="storyboards" resourceId="SEG-1" />);
      await user.click(screen.getByRole("button", { name: /版本/ }));
      fireEvent.click(await screen.findByRole("button", { name: "将 v1 移入项目回收站" }));
      fireEvent.click(screen.getByRole("button", { name: "v1：移入项目回收站？" }));
      await user.keyboard("{Escape}");
      expect(screen.getByText("历史版本")).toBeInTheDocument();
      await act(() => pending.resolve(deleted));
    });

    it("disables deletion while the resource is busy, explaining why", async () => {
      mockVersions();
      const remove = vi.spyOn(API, "deleteFileViewEntry").mockResolvedValue(deleted);
      renderOpen({ busy: true });
      const hints = await screen.findAllByRole("button", { name: "生成或编辑进行中，暂无法删除版本" });
      expect(hints).toHaveLength(2); // v1、v3；当前版本 v2 的原因优先说「当前版本」
      expect(hints[0]).toHaveAttribute("aria-disabled", "true");
      fireEvent.click(hints[0]);
      expect(screen.queryByRole("button", { name: /移入项目回收站？/ })).not.toBeInTheDocument();
      expect(remove).not.toHaveBeenCalled();
    });

    it("rechecks occupancy when confirming and refuses if the resource became busy", async () => {
      mockVersions();
      const remove = vi.spyOn(API, "deleteFileViewEntry").mockResolvedValue(deleted);
      renderOpen({ checkBusy: () => true });
      fireEvent.click(await screen.findByRole("button", { name: "将 v1 移入项目回收站" }));
      fireEvent.click(screen.getByRole("button", { name: "v1：移入项目回收站？" }));
      await waitFor(() =>
        expect(useAppStore.getState().toast?.text).toBe("生成或编辑进行中，暂无法删除版本"),
      );
      expect(remove).not.toHaveBeenCalled();
    });

    it("offers no delete entry in read-only mode", async () => {
      mockVersions();
      renderOpen({ readOnly: true });
      await screen.findByRole("button", { name: "v1" });
      expect(screen.queryByRole("button", { name: /回收站|不能删除/ })).not.toBeInTheDocument();
    });
  });
});
