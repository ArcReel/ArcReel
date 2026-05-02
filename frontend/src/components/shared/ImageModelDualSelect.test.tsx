import { render, screen } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import "@/i18n";
import { ImageModelDualSelect } from "./ImageModelDualSelect";

const OPTIONS = ["gemini/imagen-4", "ark/image-x1"];
const PROVIDER_NAMES = { gemini: "Gemini", ark: "Ark" };

describe("ImageModelDualSelect", () => {
  it("renders two combobox elements (T2I and I2I dropdowns)", () => {
    render(
      <ImageModelDualSelect
        valueT2I=""
        valueI2I=""
        options={OPTIONS}
        providerNames={PROVIDER_NAMES}
        onChange={() => {}}
      />,
    );
    const comboboxes = screen.getAllByRole("combobox");
    expect(comboboxes).toHaveLength(2);
  });

  it("calls onChange with updated t2i value when T2I dropdown changes, preserving i2i", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      <ImageModelDualSelect
        valueT2I=""
        valueI2I="ark/image-x1"
        options={OPTIONS}
        providerNames={PROVIDER_NAMES}
        onChange={onChange}
      />,
    );

    const [t2iTrigger] = screen.getAllByRole("combobox");
    await user.click(t2iTrigger);
    const imagenOption = screen.getByRole("option", { name: /imagen-4/ });
    await user.click(imagenOption);

    expect(onChange).toHaveBeenCalledWith({
      t2i: "gemini/imagen-4",
      i2i: "ark/image-x1",
    });
  });

  it("calls onChange with updated i2i value when I2I dropdown changes, preserving t2i", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      <ImageModelDualSelect
        valueT2I="gemini/imagen-4"
        valueI2I=""
        options={OPTIONS}
        providerNames={PROVIDER_NAMES}
        onChange={onChange}
      />,
    );

    const [, i2iTrigger] = screen.getAllByRole("combobox");
    await user.click(i2iTrigger);
    const arkOption = screen.getByRole("option", { name: /image-x1/ });
    await user.click(arkOption);

    expect(onChange).toHaveBeenCalledWith({
      t2i: "gemini/imagen-4",
      i2i: "ark/image-x1",
    });
  });
});
