import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ResolutionPicker } from "./ResolutionPicker";

describe("ResolutionPicker", () => {
  it("select mode renders options + default and maps empty to null", () => {
    const onChange = vi.fn();
    render(
      <ResolutionPicker
        mode="select"
        options={["720p", "1080p"]}
        value={null}
        onChange={onChange}
        placeholder="默认（不传）"
      />
    );
    const select = screen.getByRole("combobox");
    expect(select).toBeInTheDocument();
    expect(screen.getByText("默认（不传）")).toBeInTheDocument();
    fireEvent.change(select, { target: { value: "720p" } });
    expect(onChange).toHaveBeenCalledWith("720p");
    fireEvent.change(select, { target: { value: "" } });
    expect(onChange).toHaveBeenLastCalledWith(null);
  });

  it("empty options not rendered", () => {
    const { container } = render(
      <ResolutionPicker
        mode="select"
        options={[]}
        value={null}
        onChange={() => {}}
      />
    );
    expect(container.firstChild).toBeNull();
  });

  it("combobox mode allows custom input", () => {
    const onChange = vi.fn();
    render(
      <ResolutionPicker
        mode="combobox"
        options={["720p", "1080p", "4K"]}
        value={null}
        onChange={onChange}
        placeholder="默认（不传）"
      />
    );
    const input = screen.getByRole("combobox");
    fireEvent.change(input, { target: { value: "1024x1024" } });
    expect(onChange).toHaveBeenCalledWith("1024x1024");
    fireEvent.change(input, { target: { value: "" } });
    expect(onChange).toHaveBeenLastCalledWith(null);
  });
});
