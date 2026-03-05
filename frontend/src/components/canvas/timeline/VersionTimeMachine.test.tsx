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
    fireEvent.click(screen.getByRole("button", { name: /版本管理/i }));

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
      expect(screen.getByText("当前 v1")).toBeInTheDocument();
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

    fireEvent.click(screen.getByRole("button", { name: /版本管理/i }));
    expect(await screen.findByRole("button", { name: "v1" })).toBeInTheDocument();

    // Click v1 pill to preview
    fireEvent.click(screen.getByRole("button", { name: "v1" }));

    const previewImage = await screen.findByAltText("版本 v1 预览");
    expect(previewImage).toHaveClass("object-contain");
    expect(previewImage.parentElement).toHaveClass("h-80");
  });
});
