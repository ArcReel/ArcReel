import { useState } from "react";
import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import "@/i18n";
import { DurationTierPicker } from "./DurationTierPicker";

describe("DurationTierPicker", () => {
  it("picks a tier and reports a custom whole number of seconds", () => {
    const onChange = vi.fn();
    render(<DurationTierPicker value={60} onChange={onChange} />);
    expect(screen.getByRole("radio", { name: "60 秒" })).toBeChecked();

    fireEvent.click(screen.getByRole("radio", { name: "15 秒" }));
    expect(onChange).toHaveBeenLastCalledWith(15);

    fireEvent.click(screen.getByRole("radio", { name: "自定义" }));
    fireEvent.change(screen.getByRole("spinbutton", { name: "自定义目标总时长（秒）" }), { target: { value: "45" } });
    expect(onChange).toHaveBeenLastCalledWith(45);
  });

  it("reports null and explains why while the custom value is not a positive whole number", () => {
    function Harness() {
      const [value, setValue] = useState<number | null>(60);
      return <DurationTierPicker value={value} onChange={setValue} />;
    }
    render(<Harness />);
    fireEvent.click(screen.getByRole("radio", { name: "自定义" }));
    expect(screen.getByRole("alert")).toHaveTextContent("目标总时长须为正整数秒");
    fireEvent.change(screen.getByRole("spinbutton"), { target: { value: "2.5" } });
    expect(screen.getByRole("alert")).toBeInTheDocument();
    fireEvent.change(screen.getByRole("spinbutton"), { target: { value: "20" } });
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("follows an external value outside the tiers by switching to custom", () => {
    const { rerender } = render(<DurationTierPicker value={30} onChange={() => {}} />);
    rerender(<DurationTierPicker value={75} onChange={() => {}} />);
    expect(screen.getByRole("radio", { name: "自定义" })).toBeChecked();
    expect(screen.getByRole("spinbutton")).toHaveValue(75);

    rerender(<DurationTierPicker value={90} onChange={() => {}} />);
    expect(screen.getByRole("radio", { name: "90 秒" })).toBeChecked();
    expect(screen.queryByRole("spinbutton")).not.toBeInTheDocument();
  });
});
