import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { LeaveGuardProvider } from "@/components/shared/edit-unit/LeaveGuard";
import { makeNarrationSegment } from "@/test/factories";
import type { NarrationEpisodeScript } from "@/types";
import { GridImageToVideoCanvas } from "./GridImageToVideoCanvas";

/** 父级空态切换也不能卸载有修改的最后一个分镜。 */
describe("GridImageToVideoCanvas 外部删除最后一个分镜", () => {
  it("最后一镜消失时继续显示编辑器，放弃后才显示空态", () => {
    const script: NarrationEpisodeScript = { episode: 1, title: "第一集", content_mode: "narration", novel: { title: "小说", chapter: "第一章" }, segments: [makeNarrationSegment({ image_prompt: "原画面" })] };
    const page = (value: NarrationEpisodeScript) => <LeaveGuardProvider><GridImageToVideoCanvas view="board" onViewChange={vi.fn()} projectName="demo" episode={1} projectData={{ title: "测试", style: "", content_mode: "narration", characters: {}, episodes: [{ episode: 1, title: "第一集", script_file: "episode_1.json" }] }} episodeScript={value} scriptFile="episode_1.json" onUpdatePrompt={vi.fn().mockResolvedValue(true)} /></LeaveGuardProvider>;
    const { rerender } = render(page(script));
    fireEvent.change(screen.getByDisplayValue("原画面"), { target: { value: "保留最后一镜的修改" } });
    rerender(page({ ...script, segments: [] }));
    expect(screen.getByDisplayValue("保留最后一镜的修改")).toBeInTheDocument();
    expect(screen.getByText(/这个分镜已被删除/)).toHaveAttribute("role", "status");
    fireEvent.click(screen.getByRole("button", { name: "放弃修改" }));
    expect(screen.queryByDisplayValue("保留最后一镜的修改")).not.toBeInTheDocument();
    expect(screen.getByText("本集还没有分镜" )).toBeInTheDocument();
  });
});
