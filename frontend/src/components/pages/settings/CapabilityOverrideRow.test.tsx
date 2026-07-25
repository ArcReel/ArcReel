import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import "@/i18n";
import { CapabilityOverrideRow, overrideToState, stateToOverride } from "./CapabilityOverrideRow";

function setup(props: Partial<React.ComponentProps<typeof CapabilityOverrideRow>> = {}) {
  const onChange = vi.fn();
  render(
    <CapabilityOverrideRow
      override={undefined}
      systemValue={false}
      endImageCapable={true}
      onChange={onChange}
      {...props}
    />,
  );
  return { onChange };
}

const seg = (name: RegExp) => screen.getByRole("radio", { name });

describe("三态与覆盖值的映射", () => {
  it("键缺席映射为跟随，true/false 映射为强制开/关", () => {
    expect(overrideToState(undefined)).toBe("follow");
    expect(overrideToState(true)).toBe("on");
    expect(overrideToState(false)).toBe("off");
  });

  it("跟随回写 undefined（键缺席）而非 false", () => {
    expect(stateToOverride("follow")).toBeUndefined();
    expect(stateToOverride("on")).toBe(true);
    expect(stateToOverride("off")).toBe(false);
  });
});

describe("CapabilityOverrideRow", () => {
  it("默认选中跟随判定，并把系统判定值内联在段内", () => {
    setup({ systemValue: true });
    expect(seg(/跟随判定/)).toHaveAttribute("aria-checked", "true");
    expect(seg(/跟随判定/)).toHaveTextContent("支持");
  });

  it("判定值未知时显示待判定，不猜一个支持/不支持出来", () => {
    setup({ systemValue: null });
    expect(seg(/跟随判定/)).toHaveTextContent("待判定");
  });

  it("跟随态不出现已覆盖徽标与生效值小字", () => {
    setup();
    expect(screen.queryByText("已覆盖")).not.toBeInTheDocument();
    expect(screen.queryByText(/生效值/)).not.toBeInTheDocument();
  });

  it("偏离跟随时才出现徽标与生效值小字，并如实标注判定值", () => {
    setup({ override: true, systemValue: false });
    expect(screen.getByText("已覆盖")).toBeInTheDocument();
    expect(screen.getByText("生效值：支持（判定：不支持）")).toBeInTheDocument();
  });

  it("点强制关回写 false，点回跟随回写 undefined", async () => {
    const user = userEvent.setup();
    const { onChange } = setup();
    await user.click(seg(/强制关/));
    expect(onChange).toHaveBeenCalledWith(false);
  });

  it("从覆盖态点回跟随，回写 undefined 让调用方移除该键", async () => {
    const user = userEvent.setup();
    const { onChange } = setup({ override: true });
    await user.click(seg(/跟随判定/));
    expect(onChange).toHaveBeenCalledWith(undefined);
  });

  it("endpoint 不下传尾帧时禁用强制开，并给出可见的禁用原因", () => {
    setup({ endImageCapable: false });
    // 后端对这类 endpoint 的 last_frame=true 覆盖返回 422，控件先一步挡住而不是让用户撞上
    expect(seg(/强制开/)).toBeDisabled();
    expect(seg(/强制关/)).toBeEnabled();
    expect(screen.getByText(/该接口不下传尾帧/)).toBeInTheDocument();
  });

  it("禁用的强制开点不动，不产生写入", async () => {
    const user = userEvent.setup();
    const { onChange } = setup({ endImageCapable: false });
    await user.click(seg(/强制开/));
    expect(onChange).not.toHaveBeenCalled();
  });
});
