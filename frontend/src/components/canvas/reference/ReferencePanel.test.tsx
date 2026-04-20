import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ReferencePanel } from "./ReferencePanel";
import { useProjectsStore } from "@/stores/projects-store";
import type { ProjectData } from "@/types";
import type { ReferenceResource } from "@/types/reference-video";

const PROJECT: ProjectData = {
  title: "p",
  content_mode: "narration",
  style: "",
  episodes: [],
  characters: { 主角: { description: "" } },
  scenes: { 酒馆: { description: "" } },
  props: { 长剑: { description: "" } },
};

beforeEach(() => {
  useProjectsStore.setState({ currentProjectName: "proj", currentProjectData: PROJECT });
});

describe("ReferencePanel", () => {
  it("renders an empty state when there are no references", () => {
    render(
      <ReferencePanel
        references={[]}
        projectName="proj"
        onReorder={vi.fn()}
        onRemove={vi.fn()}
        onAdd={vi.fn()}
      />,
    );
    expect(screen.getByText(/No references yet|暂无引用/)).toBeInTheDocument();
  });

  it("renders a pill per reference with index marker [图N]", () => {
    const refs: ReferenceResource[] = [
      { type: "character", name: "主角" },
      { type: "scene", name: "酒馆" },
    ];
    render(
      <ReferencePanel
        references={refs}
        projectName="proj"
        onReorder={vi.fn()}
        onRemove={vi.fn()}
        onAdd={vi.fn()}
      />,
    );
    expect(screen.getByText(/\[图1\]/)).toBeInTheDocument();
    expect(screen.getByText(/\[图2\]/)).toBeInTheDocument();
    expect(screen.getByText(/主角/)).toBeInTheDocument();
    expect(screen.getByText(/酒馆/)).toBeInTheDocument();
  });

  it("calls onRemove when the ✕ button is clicked", () => {
    const onRemove = vi.fn();
    render(
      <ReferencePanel
        references={[{ type: "character", name: "主角" }]}
        projectName="proj"
        onReorder={vi.fn()}
        onRemove={onRemove}
        onAdd={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /Remove reference|移除引用/ }));
    expect(onRemove).toHaveBeenCalledWith({ type: "character", name: "主角" });
  });

  it("toggles the internal MentionPicker when the + button is clicked", () => {
    render(
      <ReferencePanel
        references={[]}
        projectName="proj"
        onReorder={vi.fn()}
        onRemove={vi.fn()}
        onAdd={vi.fn()}
      />,
    );
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Add reference|添加引用/ }));
    expect(screen.getByRole("listbox")).toBeInTheDocument();
  });

  it("calls onAdd with the selected ref when a picker option is clicked", () => {
    const onAdd = vi.fn();
    render(
      <ReferencePanel
        references={[]}
        projectName="proj"
        onReorder={vi.fn()}
        onRemove={vi.fn()}
        onAdd={onAdd}
      />,
    );
    // Open the picker first
    fireEvent.click(screen.getByRole("button", { name: /Add reference|添加引用/ }));
    // Pick "主角" (from the stubbed PROJECT in this test file's beforeEach)
    fireEvent.click(screen.getByRole("option", { name: /主角/ }));
    expect(onAdd).toHaveBeenCalledWith({ type: "character", name: "主角" });
  });
});

describe("ReferencePanel drag a11y", () => {
  const baseProject: ProjectData = {
    title: "p",
    content_mode: "narration",
    style: "",
    episodes: [],
    characters: { 张三: { description: "" } },
    scenes: { 酒馆: { description: "" } },
    props: {},
  };

  it("renders sr-only drag instructions via DndContext accessibility", () => {
    useProjectsStore.setState({ currentProjectName: "p", currentProjectData: baseProject });
    render(
      <ReferencePanel
        references={[{ type: "character", name: "张三" }]}
        projectName="p"
        onReorder={vi.fn()}
        onRemove={vi.fn()}
        onAdd={vi.fn()}
      />,
    );
    // dnd-kit 会把 `screenReaderInstructions.draggable` 文本渲染为 sr-only 的段落，id 形如 "DndDescribedBy-..."
    expect(
      screen.getByText(/按 Space 键拿起|Press Space to pick up/i),
    ).toBeInTheDocument();
  });
});
