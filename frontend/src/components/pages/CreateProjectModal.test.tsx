import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Router } from "wouter";
import { memoryLocation } from "wouter/memory-location";
import { API } from "@/api";
import { CreateProjectModal } from "@/components/pages/CreateProjectModal";
import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";

function renderModal() {
  const location = memoryLocation({ path: "/app/projects" });
  return render(
    <Router hook={location.hook}>
      <CreateProjectModal />
    </Router>,
  );
}

describe("CreateProjectModal", () => {
  beforeEach(() => {
    useProjectsStore.setState(useProjectsStore.getInitialState(), true);
    useProjectsStore.setState({ showCreateModal: true });
    useAppStore.setState(useAppStore.getInitialState(), true);
    vi.restoreAllMocks();
  });

  it("submits only the project title and uses backend-generated name", async () => {
    vi.spyOn(API, "createProject").mockResolvedValue({
      success: true,
      name: "project-aa11bb22",
      project: {
        title: "演示项目",
        content_mode: "narration",
        style: "Photographic",
        episodes: [],
        characters: {},
        clues: {},
      },
    });

    renderModal();

    const submitButton = screen.getByRole("button", { name: "创建项目" });
    expect(submitButton).toBeDisabled();

    fireEvent.change(screen.getByPlaceholderText("例如：重生之皇后威武"), {
      target: { value: "演示项目" },
    });

    expect(submitButton).toBeEnabled();
    fireEvent.click(submitButton);

    await waitFor(() => {
      expect(API.createProject).toHaveBeenCalledWith({
        title: "演示项目",
        content_mode: "narration",
        aspect_ratio: "9:16",
        default_duration: null,
        generation_mode: "single",
      });
    });
  });

  it("disables submission when the project title is empty", () => {
    vi.spyOn(API, "createProject").mockResolvedValue({
      success: true,
      name: "project-aa11bb22",
      project: {
        title: "演示项目",
        content_mode: "narration",
        style: "Photographic",
        episodes: [],
        characters: {},
        clues: {},
      },
    });

    renderModal();

    expect(screen.getByRole("button", { name: "创建项目" })).toBeDisabled();
    expect(API.createProject).not.toHaveBeenCalled();
  });
});
