import { act, fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { CancelConfirmDialog } from "./CancelConfirmDialog";

const request = { kind: "all" as const, projectName: "demo", queuedCount: 2 };

function renderWithTrigger(props: { cancelling?: boolean; onDismiss?: () => void } = {}) {
  const onDismiss = props.onDismiss ?? vi.fn();
  const view = render(
    <>
      <button type="button">trigger</button>
      <button type="button">elsewhere</button>
      <div id="slot" />
    </>,
  );
  const trigger = screen.getByRole("button", { name: "trigger" });
  // 预览返回前焦点停在触发按钮上
  trigger.focus();
  const dialog = (cancelling: boolean) => (
    <CancelConfirmDialog
      request={request}
      cancelling={cancelling}
      failed={false}
      onConfirm={vi.fn().mockResolvedValue(undefined)}
      onDismiss={onDismiss}
    />
  );
  const confirm = render(dialog(props.cancelling ?? false));
  return { ...view, trigger, onDismiss, rerenderDialog: (cancelling: boolean) => confirm.rerender(dialog(cancelling)) };
}

describe("CancelConfirmDialog", () => {
  it("moves focus to the back button when it opens", () => {
    renderWithTrigger();
    expect(screen.getByRole("button", { name: "返回" })).toHaveFocus();
  });

  it("returns focus to the trigger on Escape", () => {
    const { trigger, onDismiss } = renderWithTrigger();
    fireEvent.keyDown(screen.getByRole("button", { name: "返回" }), { key: "Escape" });
    expect(onDismiss).toHaveBeenCalledTimes(1);
    expect(trigger).toHaveFocus();
  });

  it("dismisses without moving focus when focus leaves the confirmation", () => {
    const { onDismiss } = renderWithTrigger();
    const elsewhere = screen.getByRole("button", { name: "elsewhere" });
    act(() => elsewhere.focus());
    expect(onDismiss).toHaveBeenCalledTimes(1);
    expect(elsewhere).toHaveFocus();
  });

  it("disables both buttons while cancelling and refocuses back once the request settles", () => {
    const { onDismiss, rerenderDialog } = renderWithTrigger();
    rerenderDialog(true);
    const back = screen.getByRole("button", { name: "返回" });
    expect(back).toBeDisabled();
    expect(screen.getByRole("button", { name: "取消中…" })).toBeDisabled();
    act(() => screen.getByRole("button", { name: "elsewhere" }).focus());
    expect(onDismiss).not.toHaveBeenCalled();

    rerenderDialog(false);
    expect(back).toHaveFocus();
  });
});
