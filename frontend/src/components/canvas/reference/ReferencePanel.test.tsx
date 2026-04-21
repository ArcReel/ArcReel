import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
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

  // Pill 现在是竖排卡片：thumbnail 上、名称下（无 @ 前缀）。[图N] 索引文案已废弃——
  // 顺序本身就是 [图N]，额外文字是冗余。
  it("renders a pill per reference with the plain asset name (no @ prefix)", () => {
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
    expect(screen.getByText("主角")).toBeInTheDocument();
    expect(screen.getByText("酒馆")).toBeInTheDocument();
    // @前缀应被剥离
    expect(screen.queryByText("@主角")).not.toBeInTheDocument();
    expect(screen.queryByText(/\[图1\]/)).not.toBeInTheDocument();
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

  // 缩略图增大到 40x40，并作为 button 暴露——点击打开 lightbox，ESC/背景关闭。
  it("zooms the thumbnail into a lightbox; ESC closes it", async () => {
    useProjectsStore.setState({
      currentProjectName: "proj",
      currentProjectData: {
        ...PROJECT,
        characters: { 张三: { description: "", character_sheet: "characters/zs.png" } },
      } as ProjectData,
    });
    render(
      <ReferencePanel
        references={[{ type: "character", name: "张三" }]}
        projectName="proj"
        onReorder={vi.fn()}
        onRemove={vi.fn()}
        onAdd={vi.fn()}
      />,
    );
    const zoomBtn = screen.getByRole("button", { name: /View larger thumbnail|放大查看/ });
    fireEvent.click(zoomBtn);
    const dialog = screen.getByRole("dialog");
    expect(dialog).toBeInTheDocument();
    const closeBtns = screen.getAllByRole("button", { name: /Close preview|关闭.*预览/ });
    expect(closeBtns.length).toBeGreaterThan(0);
    // ESC 关闭：ImageLightbox 的 useEscapeClose 挂在 document，不能 fire 到 window
    fireEvent.keyDown(document, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
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
