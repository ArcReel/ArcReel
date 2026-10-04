import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { LeaveGuardProvider } from "@/components/shared/edit-unit/LeaveGuard";
import { makeNarrationSegment } from "@/test/factories";
import { ShotSplitView } from "./ShotSplitView";

/** 分镜视图的切换拦截与快捷键：J / K 切换分镜，⌘S / Ctrl+S 保存当前分镜。 */

const segments = [
  makeNarrationSegment({ segment_id: "E1S01", image_prompt: "雨夜街道" }),
  makeNarrationSegment({ segment_id: "E1S02", image_prompt: "清晨码头" }),
];

function renderView(onUpdatePrompt = vi.fn().mockResolvedValue(true)) {
  render(
    <LeaveGuardProvider>
      <ShotSplitView
        segments={segments}
        contentMode="narration"
        aspectRatio="9:16"
        projectName="demo"
        scriptFile="episode_1.json"
        onUpdatePrompt={onUpdatePrompt}
        durationOptions={[8]}
      />
    </LeaveGuardProvider>,
  );
  return onUpdatePrompt;
}

const position = () => screen.getByText(/^\d \/ 2$/).textContent;

describe("ShotSplitView 切换拦截与快捷键", () => {
  it("改了提示词后按 J 先询问，放弃修改后切到下一个分镜且不提交", async () => {
    const onUpdatePrompt = renderView();
    fireEvent.change(screen.getByDisplayValue("雨夜街道"), { target: { value: "雨后的街道" } });

    fireEvent.keyDown(document.body, { key: "j" });
    const dialog = await screen.findByRole("alertdialog", { name: "「S01」有未保存的修改" });
    expect(within(dialog).getByRole("button", { name: "保存并切换" })).toBeInTheDocument();
    expect(position()).toBe("1 / 2");

    fireEvent.click(within(dialog).getByRole("button", { name: "放弃修改" }));

    await waitFor(() => expect(position()).toBe("2 / 2"));
    expect(screen.getByDisplayValue("清晨码头")).toBeInTheDocument();
    expect(onUpdatePrompt).not.toHaveBeenCalled();
  });

  it("没有未保存修改时 J / K 直接切到下一个与上一个分镜", () => {
    renderView();

    fireEvent.keyDown(document.body, { key: "j" });
    expect(position()).toBe("2 / 2");
    fireEvent.keyDown(document.body, { key: "k" });
    expect(position()).toBe("1 / 2");
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
  });

  it("焦点在输入框里或输入法组合输入中时 J 不切换分镜", () => {
    renderView();

    fireEvent.keyDown(screen.getByDisplayValue("雨夜街道"), { key: "j" });
    fireEvent.keyDown(document.body, { key: "j", isComposing: true });

    expect(position()).toBe("1 / 2");
  });

  it("⌘S 与 Ctrl+S 在输入框里也保存当前分镜，并拦下浏览器的保存网页", async () => {
    const onUpdatePrompt = renderView();
    const box = screen.getByDisplayValue("雨夜街道");
    fireEvent.change(box, { target: { value: "雨后的街道" } });

    expect(fireEvent.keyDown(box, { key: "s", metaKey: true })).toBe(false);
    await waitFor(() => expect(onUpdatePrompt).toHaveBeenCalledWith("E1S01", { image_prompt: "雨后的街道" }));

    fireEvent.change(box, { target: { value: "雨停后的街道" } });
    expect(fireEvent.keyDown(box, { key: "s", ctrlKey: true })).toBe(false);
    await waitFor(() => expect(onUpdatePrompt).toHaveBeenLastCalledWith("E1S01", { image_prompt: "雨停后的街道" }));
  });

  it("分镜列表底部常驻快捷键提示", () => {
    renderView();

    const list = screen.getByRole("navigation", { name: "分镜列表" });
    expect(within(list).getByText("切换分镜")).toBeInTheDocument();
    expect(within(list).getByText("保存")).toBeInTheDocument();
  });
});
