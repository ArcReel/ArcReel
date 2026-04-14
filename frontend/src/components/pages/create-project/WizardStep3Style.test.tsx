import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import "@/i18n";
import { WizardStep3Style } from "./WizardStep3Style";

const baseValue = {
  mode: "template" as const,
  templateId: "live_premium_drama",
  activeCategory: "live" as const,
  uploadedFile: null,
  uploadedPreview: null,
};

const noop = () => {};
const commonProps = { onBack: noop, onCreate: noop, onCancel: noop, creating: false };

describe("WizardStep3Style", () => {
  it("renders live templates in default live tab with default one selected", () => {
    render(<WizardStep3Style value={baseValue} onChange={noop} {...commonProps} />);
    // The default template gets a "default" badge
    expect(screen.getAllByText(/（默认）|\(default\)/i).length).toBeGreaterThanOrEqual(1);
  });

  it("emits onChange with new templateId when a template card is clicked", () => {
    const onChange = vi.fn();
    render(<WizardStep3Style value={baseValue} onChange={onChange} {...commonProps} />);
    // Click a different live template by its i18n name (e.g. 张艺谋风格)
    const card = screen.getByRole("button", { name: /张艺谋/ });
    fireEvent.click(card);
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({
      mode: "template",
      templateId: "live_zhang_yimou",
    }));
  });

  it("switches to custom mode and clears templateId when custom tab clicked", () => {
    const onChange = vi.fn();
    render(<WizardStep3Style value={baseValue} onChange={onChange} {...commonProps} />);
    fireEvent.click(screen.getByRole("button", { name: /自定义|Custom/ }));
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({
      mode: "custom",
      templateId: null,
    }));
  });

  it("switches to anim category and selects the first anim template when anim tab clicked from live", () => {
    const onChange = vi.fn();
    render(<WizardStep3Style value={baseValue} onChange={onChange} {...commonProps} />);
    fireEvent.click(screen.getByRole("button", { name: /漫剧|Animation/ }));
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({
      mode: "template",
      activeCategory: "anim",
      templateId: "anim_3d_cg",   // first anim template id
    }));
  });

  it("disables Create button when custom mode has no uploaded file", () => {
    const value = { ...baseValue, mode: "custom" as const, templateId: null };
    render(<WizardStep3Style value={value} onChange={noop} {...commonProps} />);
    const createBtn = screen.getByRole("button", { name: /创建项目|Create/i });
    expect(createBtn).toBeDisabled();
  });

  it("enables Create button when custom mode has uploaded file", () => {
    const value = {
      ...baseValue,
      mode: "custom" as const,
      templateId: null,
      uploadedFile: new File([""], "x.png", { type: "image/png" }),
      uploadedPreview: "blob:test",
    };
    render(<WizardStep3Style value={value} onChange={noop} {...commonProps} />);
    const createBtn = screen.getByRole("button", { name: /创建项目|Create/i });
    expect(createBtn).toBeEnabled();
  });

  it("disables Create button while creating=true", () => {
    render(<WizardStep3Style value={baseValue} onChange={noop} {...{ ...commonProps, creating: true }} />);
    // While creating, button reads "创建中…" / "Creating…"
    const createBtn = screen.getByRole("button", { name: /创建中|Creating|创建项目|Create/i });
    expect(createBtn).toBeDisabled();
  });

  it("calls onBack when Back is clicked", () => {
    const onBack = vi.fn();
    render(<WizardStep3Style value={baseValue} onChange={noop} {...commonProps} onBack={onBack} />);
    fireEvent.click(screen.getByRole("button", { name: /上一步|Back/ }));
    expect(onBack).toHaveBeenCalledOnce();
  });

  it("calls onCancel when Cancel is clicked", () => {
    const onCancel = vi.fn();
    render(<WizardStep3Style value={baseValue} onChange={noop} {...commonProps} onCancel={onCancel} />);
    fireEvent.click(screen.getByRole("button", { name: /取消|Cancel/ }));
    expect(onCancel).toHaveBeenCalledOnce();
  });
});
