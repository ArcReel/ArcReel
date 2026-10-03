import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ResolutionPicker } from "./ResolutionPicker";

describe("ResolutionPicker", () => {
  it("select mode renders options + default and maps empty to null", async () => {
    const user = userEvent.setup();
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
    expect(select).toHaveTextContent("默认（不传）");
    await user.click(select);
    await user.click(await screen.findByRole("option", { name: "720p" }));
    expect(onChange).toHaveBeenCalledWith("720p");
    await user.click(select);
    await user.click(await screen.findByRole("option", { name: "默认（不传）" }));
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
    expect(container).toBeEmptyDOMElement();
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
