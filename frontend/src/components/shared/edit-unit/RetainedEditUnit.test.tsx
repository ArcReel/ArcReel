import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { Link, Router } from "wouter";
import { memoryLocation } from "wouter/memory-location";
import { createDeferred } from "@/test/deferred";
import { LeaveGuardProvider, useConfirmLeave } from "./LeaveGuard";
import { RetainedEditUnit } from "./RetainedEditUnit";
import { UnsavedChangesBar } from "./UnsavedChangesBar";
import { useEditUnit } from "./useEditUnit";

const allowNavigation = () => true;
const save = vi.fn<(value: string) => Promise<void>>();
function Editor({ source }: { source: string }) {
  const unit = useEditUnit({ source, save, allowNavigation });
  return <><label>正文<textarea value={unit.value} onChange={(event) => unit.setValue(event.target.value)} /></label><UnsavedChangesBar unit={unit} /></>;
}

describe("RetainedEditUnit 主动离开", () => {
  it("外部替换保留旧视图后，旧 allowNavigation 不再放行会离开的导航", async () => {
    const location = memoryLocation({ path: "/notes", record: true });
    const page = (identity: string, source: string) => <Router hook={location.hook}><LeaveGuardProvider><Link href="/elsewhere">去新视图</Link><RetainedEditUnit identity={identity} value={source} message="外部替换了正文">{(shown) => <Editor source={shown} />}</RetainedEditUnit></LeaveGuardProvider></Router>;
    const { rerender } = render(page("notes", "原文"));
    fireEvent.change(screen.getByRole("textbox", { name: "正文" }), { target: { value: "未保存正文" } });
    rerender(page("replaced", "外部的新内容"));
    fireEvent.click(screen.getByRole("link", { name: "去新视图" }));
    const dialog = await screen.findByRole("alertdialog");
    expect(location.history).toEqual(["/notes"]);
    expect(screen.getByDisplayValue("未保存正文")).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: "放弃修改" }));
    expect(location.history).toEqual(["/notes", "/elsewhere"]);
    expect(screen.getByDisplayValue("外部的新内容")).toBeInTheDocument();
  });

  it("同一视图里另一个单元放弃修改后，仍因有修改的单元保留旧视图", () => {
    function Two({ source }: { source: string }) {
      const first = useEditUnit({ source, save });
      const second = useEditUnit({ source: "备注", save });
      return (
        <>
          <label>正文<textarea value={first.value} onChange={(event) => first.setValue(event.target.value)} /></label>
          <label>备注<textarea value={second.value} onChange={(event) => second.setValue(event.target.value)} /></label>
          <button type="button" onClick={second.discard}>放弃备注</button>
          <output>{source}</output>
        </>
      );
    }
    const page = (identity: string, source: string) => <LeaveGuardProvider><RetainedEditUnit identity={identity} value={source} message="外部替换了正文">{(shown) => <Two source={shown} />}</RetainedEditUnit></LeaveGuardProvider>;
    const { rerender } = render(page("notes", "原文"));
    fireEvent.change(screen.getByRole("textbox", { name: "正文" }), { target: { value: "未保存正文" } });
    fireEvent.change(screen.getByRole("textbox", { name: "备注" }), { target: { value: "未保存备注" } });
    fireEvent.click(screen.getByRole("button", { name: "放弃备注" }));

    rerender(page("replaced", "外部的新内容"));

    expect(screen.getByRole("status")).toHaveTextContent("原文");
  });

  describe("放弃修改后等待落定的删除", () => {
    function RemoveButton({ action }: { action: () => Promise<boolean> }) {
      const confirmLeave = useConfirmLeave();
      return <button type="button" onClick={() => confirmLeave(action)}>删除正文</button>;
    }
    const page = (identity: string, source: string, action: () => Promise<boolean>) => (
      <LeaveGuardProvider>
        <RemoveButton action={action} />
        <RetainedEditUnit identity={identity} value={source} message="外部替换了正文">
          {(shown) => <Editor key={shown} source={shown} />}
        </RetainedEditUnit>
      </LeaveGuardProvider>
    );
    const discardForRemoval = async () => {
      fireEvent.change(screen.getByRole("textbox", { name: "正文" }), { target: { value: "未保存正文" } });
      fireEvent.click(screen.getByRole("button", { name: "删除正文" }));
      fireEvent.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "放弃修改" }));
    };

    it("删除在途时单元被移除：这是删除本身的结果，直接采用真实状态，不当作外部移除保留", async () => {
      const deleting = createDeferred<boolean>();
      const action = () => deleting.promise;
      const { rerender } = render(page("notes", "原文", action));
      await discardForRemoval();

      rerender(page("next", "下一条正文", action));

      expect(screen.getByRole("textbox", { name: "正文" })).toHaveValue("下一条正文");
      expect(screen.queryByText("外部替换了正文")).not.toBeInTheDocument();
      await act(async () => deleting.resolve(true));
      expect(screen.getByRole("textbox", { name: "正文" })).toHaveValue("下一条正文");
    });

    it("删除失败后修改恢复保护，之后的外部移除照常保留", async () => {
      const action = vi.fn(async () => false);
      const { rerender } = render(page("notes", "原文", action));
      await discardForRemoval();
      await act(async () => {});
      expect(action).toHaveBeenCalledTimes(1);

      rerender(page("next", "下一条正文", action));

      expect(screen.getByRole("textbox", { name: "正文" })).toHaveValue("未保存正文");
      expect(screen.getByText("外部替换了正文")).toBeInTheDocument();
    });
  });
});
