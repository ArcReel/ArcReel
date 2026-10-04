import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { Link, Router } from "wouter";
import { memoryLocation } from "wouter/memory-location";
import { LeaveGuardProvider } from "./LeaveGuard";
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
});
