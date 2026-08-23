import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import "@/i18n";
import { API } from "@/api";
import { WizardStep3Style } from "./WizardStep3Style";

const baseValue = {
  mode: "template" as const,
  templateId: "live_premium_drama",
  activeCategory: "live" as const,
  uploadedFile: null,
  uploadedPreview: null,
  customStyleId: null,
  styleDescription: "",
};

const noop = () => {};
const commonProps = {
  onBack: noop,
  onCreate: noop,
  onCancel: noop,
  onAnalyze: noop,
  creating: false,
  analyzing: false,
  customStyles: [],
  customStylesLoading: false,
  onCustomStyleUpdated: noop,
};

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

  it("switches to custom mode while preserving templateId (切换无损失)", () => {
    const onChange = vi.fn();
    render(<WizardStep3Style value={baseValue} onChange={onChange} {...commonProps} />);
    fireEvent.click(screen.getByRole("button", { name: /自定义|Custom/ }));
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({
      mode: "custom",
      templateId: baseValue.templateId,   // 原 template 保留，回切时恢复
    }));
  });

  it("switches category tab while preserving uploaded file/preview (切换无损失)", () => {
    const onChange = vi.fn();
    const uploaded = new File([""], "x.png", { type: "image/png" });
    const valueWithUpload = {
      ...baseValue,
      mode: "custom" as const,
      uploadedFile: uploaded,
      uploadedPreview: "blob:test",
    };
    render(<WizardStep3Style value={valueWithUpload} onChange={onChange} {...commonProps} />);
    fireEvent.click(screen.getByRole("button", { name: /漫剧|Animation/ }));
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({
      mode: "template",
      activeCategory: "anim",
      uploadedFile: uploaded,
      uploadedPreview: "blob:test",
    }));
  });

  it("switches to anim tab while preserving the live templateId (cross-tab selection is not auto-overridden)", () => {
    const onChange = vi.fn();
    render(<WizardStep3Style value={baseValue} onChange={onChange} {...commonProps} />);
    fireEvent.click(screen.getByRole("button", { name: /漫剧|Animation/ }));
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({
      mode: "template",
      activeCategory: "anim",
      templateId: "live_premium_drama",   // preserved from live; anim tab shows no selection
    }));
  });

  it("keeps Create button enabled when custom mode has no uploaded file (style 为可选)", () => {
    const value = { ...baseValue, mode: "custom" as const, templateId: null };
    render(<WizardStep3Style value={value} onChange={noop} {...commonProps} />);
    const createBtn = screen.getByRole("button", { name: /创建项目|Create/i });
    expect(createBtn).not.toBeDisabled();
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

  it("shows an editable style description in custom mode", () => {
    const onChange = vi.fn();
    const value = { ...baseValue, mode: "custom" as const, templateId: null };
    render(<WizardStep3Style value={value} onChange={onChange} {...commonProps} />);

    fireEvent.change(screen.getByLabelText(/风格描述|Style description/i), {
      target: { value: "soft pastel, diffused light" },
    });

    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({
      styleDescription: "soft pastel, diffused light",
    }));
  });

  it("offers saved custom style cards and fills the selected style description", () => {
    const onChange = vi.fn();
    const value = { ...baseValue, mode: "custom" as const, templateId: null };
    render(
      <WizardStep3Style
        value={value}
        onChange={onChange}
        {...commonProps}
        customStyles={[{
          id: "saved-style",
          name: "韩剧柔光",
          description: "soft k-drama light",
          image_path: null,
          source_project: "demo",
          updated_at: null,
        }]}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "韩剧柔光" }));
    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({
      customStyleId: "saved-style",
      styleDescription: "soft k-drama light",
    }));
  });

  it("edits a saved custom style without selecting the card", async () => {
    const onChange = vi.fn();
    const onCustomStyleUpdated = vi.fn();
    const value = { ...baseValue, mode: "custom" as const, templateId: null };
    const style = {
      id: "saved-style",
      name: "韩剧柔光",
      description: "soft k-drama light",
      image_path: null,
      source_project: "demo",
      updated_at: null,
    };
    const updated = { ...style, name: "暖调纪实", description: "warm documentary light" };
    const updateSpy = vi.spyOn(API, "updateCustomStyle").mockResolvedValue({
      style: updated,
    });
    render(
      <WizardStep3Style
        value={value}
        onChange={onChange}
        {...commonProps}
        customStyles={[style]}
        onCustomStyleUpdated={onCustomStyleUpdated}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /编辑韩剧柔光|Edit 韩剧柔光/i }));
    fireEvent.change(screen.getByLabelText(/风格名称|Style name/i), {
      target: { value: "暖调纪实" },
    });
    fireEvent.change(screen.getByLabelText(/风格提示词|Style prompt/i), {
      target: { value: "warm documentary light" },
    });
    fireEvent.click(screen.getByRole("button", { name: /保存修改|Save changes/i }));

    await waitFor(() => {
      expect(updateSpy).toHaveBeenCalledWith("saved-style", expect.objectContaining({
        name: "暖调纪实",
        description: "warm documentary light",
      }));
      expect(onCustomStyleUpdated).toHaveBeenCalledWith(updated);
    });
    expect(onChange).not.toHaveBeenCalled();
  });

  it("shows Analyze on an uploaded preview and delegates explicit analysis", () => {
    const onAnalyze = vi.fn();
    const value = {
      ...baseValue,
      mode: "custom" as const,
      templateId: null,
      uploadedFile: new File(["img"], "x.png", { type: "image/png" }),
      uploadedPreview: "blob:test",
    };
    render(
      <WizardStep3Style
        value={value}
        onChange={noop}
        {...commonProps}
        onAnalyze={onAnalyze}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /解析风格|Analyze style/i }));
    expect(onAnalyze).toHaveBeenCalledOnce();
  });

  it("does not show the custom style textbox in template mode", () => {
    render(<WizardStep3Style value={baseValue} onChange={noop} {...commonProps} />);
    expect(screen.queryByLabelText(/风格描述|Style description/i)).not.toBeInTheDocument();
  });

  it("disables Create button while creating=true", () => {
    render(<WizardStep3Style value={baseValue} onChange={noop} {...{ ...commonProps, creating: true }} />);
    // While creating, button reads "创建中…" / "Creating…"
    const createBtn = screen.getByRole("button", { name: /创建中|Creating|创建项目|Create/i });
    expect(createBtn).toBeDisabled();
  });

  it("disables Create button while analyzing=true", () => {
    const value = {
      ...baseValue,
      mode: "custom" as const,
      uploadedPreview: "blob:test",
    };
    render(
      <WizardStep3Style
        value={value}
        onChange={noop}
        {...{ ...commonProps, analyzing: true }}
      />,
    );
    expect(screen.getByRole("button", { name: /创建项目|Create/i })).toBeDisabled();
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

  it("preserves null templateId when switching from custom to live tab (no auto-selection)", () => {
    const onChange = vi.fn();
    const customValue = { ...baseValue, mode: "custom" as const, templateId: null };
    render(<WizardStep3Style value={customValue} onChange={onChange} {...commonProps} />);
    fireEvent.click(screen.getByRole("button", { name: /真人剧|Live/ }));
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({
      mode: "template",
      activeCategory: "live",
      templateId: null,   // unchanged; user must explicitly click a card
    }));
  });

  it("preserves live templateId when re-clicking live tab", () => {
    const onChange = vi.fn();
    const value = { ...baseValue, templateId: "live_zhang_yimou" };
    render(<WizardStep3Style value={value} onChange={onChange} {...commonProps} />);
    fireEvent.click(screen.getByRole("button", { name: /真人剧|Live/ }));
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({
      activeCategory: "live",
      templateId: "live_zhang_yimou",
    }));
  });

  it("shows no selected card in anim tab when current templateId belongs to live (bug repro)", () => {
    // Simulate the state AFTER the (fixed) tab switch: live_premium_drama
    // stays as templateId but activeCategory moves to anim.
    const crossTabValue = { ...baseValue, activeCategory: "anim" as const };
    render(<WizardStep3Style value={crossTabValue} onChange={noop} {...commonProps} />);
    // No anim template card should be rendered as pressed/selected.
    const pressedCards = screen.queryAllByRole("button", { pressed: true });
    // The tab buttons themselves don't use aria-pressed, so this queries only template cards.
    expect(pressedCards).toHaveLength(0);
  });

  it("preserves anim templateId when re-clicking anim tab", () => {
    const onChange = vi.fn();
    const animValue = { ...baseValue, activeCategory: "anim" as const, templateId: "anim_ghibli" };
    render(<WizardStep3Style value={animValue} onChange={onChange} {...commonProps} />);
    fireEvent.click(screen.getByRole("button", { name: /漫剧|Animation/ }));
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({
      activeCategory: "anim",
      templateId: "anim_ghibli",
    }));
  });
});
