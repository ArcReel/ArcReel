import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import "@/i18n";
import { FactField } from "./ProviderModelEntryInfo";

describe("FactField", () => {
  it("stops blocking the save once the value is replaced from outside and the editor remounts", () => {
    const onValidityChange = vi.fn();
    const field = (value: number[]) => (
      <FactField
        dimension="durations"
        value={value}
        resolutions={[]}
        onChange={vi.fn()}
        validityKey="entry:durations"
        onValidityChange={onValidityChange}
        official={null}
      />
    );
    const { rerender } = render(field([4, 8]));
    fireEvent.change(screen.getByRole("textbox", { name: "「可选时长」的值" }), { target: { value: "8-4" } });
    expect(onValidityChange).toHaveBeenLastCalledWith("entry:durations", false);

    // 采用官方值、放弃修改这类外部改值
    rerender(field([5, 10]));

    expect(onValidityChange).toHaveBeenLastCalledWith("entry:durations", true);
    expect(screen.getByRole("textbox", { name: "「可选时长」的值" })).toHaveValue("5, 10");
  });
});
