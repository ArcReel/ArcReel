import { render, screen } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import type { ComponentProps } from "react";
import userEvent from "@testing-library/user-event";
import "@/i18n";
import { LayeredModelFields, effectiveModel, type LayeredSubField } from "./LayeredModelFields";

const OPTIONS = ["gemini/veo-3", "ark/seedance"];
const PROVIDER_NAMES = { gemini: "Gemini", ark: "Ark" };

function subField(overrides: Partial<LayeredSubField> = {}): LayeredSubField {
  return {
    key: "i2v",
    label: "图生视频",
    caption: "覆盖由分镜图或宫格图驱动的视频生成。",
    value: "",
    options: ["gemini/veo-3"],
    onChange: () => {},
    ...overrides,
  };
}

function renderFields(overrides: Partial<ComponentProps<typeof LayeredModelFields>> = {}) {
  return render(
    <LayeredModelFields
      defaultLabel="默认视频模型"
      defaultValue=""
      defaultOptions={OPTIONS}
      onDefaultChange={() => {}}
      emptyLabel="自动选择"
      providerNames={PROVIDER_NAMES}
      subFields={[subField()]}
      {...overrides}
    />,
  );
}

describe("effectiveModel", () => {
  it("returns the first non-empty layer", () => {
    expect(effectiveModel("", null, "ark/seedance", "gemini/veo-3")).toBe("ark/seedance");
  });

  it("returns undefined when every layer is empty (auto-inferred, not computable here)", () => {
    expect(effectiveModel("", null, undefined)).toBeUndefined();
  });
});

describe("LayeredModelFields", () => {
  it("keeps the default dropdown resident and the sub-fields collapsed", () => {
    const { container } = renderFields();
    expect(screen.getByRole("combobox", { name: "默认视频模型" })).toBeInTheDocument();
    expect(container.querySelector("details")?.open).toBe(false);
    // 收起态没有计数徽标，因为无任何细分项被指定
    expect(screen.queryByText(/已指定/)).not.toBeInTheDocument();
  });

  it("reads as following the default rather than requiring a pick when the whole chain is empty", async () => {
    // 全层皆空时演算不出具体模型：触发按钮上必须仍是空值语义，通用的「选择模型」会读成必填
    const user = userEvent.setup();
    renderFields();
    expect(screen.getByRole("combobox", { name: "默认视频模型" })).toHaveTextContent("自动选择");
    await user.click(screen.getByText("按用途指定模型"));
    expect(screen.getByRole("combobox", { name: "图生视频" })).toHaveTextContent("跟随默认");
  });

  it("does not render the disclosure at all when no sub-fields are supplied", () => {
    const { container } = renderFields({ subFields: undefined });
    expect(container.querySelector("details")).toBeNull();
    expect(screen.getAllByRole("combobox")).toHaveLength(1);
  });

  it("starts expanded and shows a count once a sub-field carries a value", () => {
    const { container } = renderFields({
      subFields: [subField({ value: "gemini/veo-3" }), subField({ key: "r2v", label: "参考生视频" })],
    });
    expect(container.querySelector("details")?.open).toBe(true);
    expect(screen.getByText("已指定 1 项")).toBeInTheDocument();
  });

  it("shows the resolved model behind 跟随默认 for an unset sub-field", async () => {
    const user = userEvent.setup();
    renderFields({ subFields: [subField({ effective: "ark/seedance" })] });
    await user.click(screen.getByText("按用途指定模型"));
    expect(screen.getByRole("combobox", { name: "图生视频" })).toHaveTextContent(
      /跟随默认 · Ark · seedance/,
    );
  });

  it("offers the unfiltered list on the default layer and the per-purpose list on a sub-field", async () => {
    const user = userEvent.setup();
    renderFields();
    await user.click(screen.getByRole("combobox", { name: "默认视频模型" }));
    expect(screen.getByRole("option", { name: /seedance/ })).toBeInTheDocument();
    await user.keyboard("{Escape}");

    await user.click(screen.getByText("按用途指定模型"));
    await user.click(screen.getByRole("combobox", { name: "图生视频" }));
    expect(screen.getByRole("option", { name: /veo-3/ })).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: /seedance/ })).not.toBeInTheDocument();
  });

  it("routes a sub-field selection to that sub-field's own onChange", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    const onDefaultChange = vi.fn();
    renderFields({ onDefaultChange, subFields: [subField({ onChange })] });
    await user.click(screen.getByText("按用途指定模型"));
    await user.click(screen.getByRole("combobox", { name: "图生视频" }));
    await user.click(screen.getByRole("option", { name: /veo-3/ }));
    expect(onChange).toHaveBeenCalledWith("gemini/veo-3");
    expect(onDefaultChange).not.toHaveBeenCalled();
  });

  it("force-opens the disclosure and shows an error notice with retry when subFieldsError is set, even with no sub-fields", () => {
    const onRetry = vi.fn();
    const { container } = renderFields({ subFields: [], subFieldsError: { onRetry } });
    // 无任何细分项本应让整块折叠区消失，但错误态下仍需展示，用户才能感知失败并重试
    expect(container.querySelector("details")).not.toBeNull();
    expect(container.querySelector("details")?.open).toBe(true);
    expect(screen.getByRole("alert")).toHaveTextContent(/可选模型列表加载失败/);
    expect(screen.queryByText("留空的用途沿用上方默认模型。")).not.toBeInTheDocument();
  });

  it("still renders degraded sub-fields alongside the error notice when some carry a saved value", async () => {
    const user = userEvent.setup();
    const onRetry = vi.fn();
    renderFields({
      subFields: [subField({ value: "gemini/veo-3" })],
      subFieldsError: { onRetry },
    });
    expect(screen.getByRole("combobox", { name: "图生视频" })).toHaveTextContent("veo-3");
    await user.click(screen.getByRole("button", { name: "重试" }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("disables the retry button while a retry is in flight", () => {
    renderFields({ subFields: [], subFieldsError: { onRetry: () => {}, retrying: true } });
    expect(screen.getByRole("button", { name: "重试" })).toBeDisabled();
  });

  it("lets the user collapse the disclosure again after the forced-open error state", async () => {
    const user = userEvent.setup();
    const { container } = renderFields({ subFields: [], subFieldsError: { onRetry: () => {} } });
    expect(container.querySelector("details")?.open).toBe(true);
    await user.click(screen.getByText("按用途指定模型"));
    expect(container.querySelector("details")?.open).toBe(false);
  });
});
