import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import "@/i18n";
import { ProviderModelSelect } from "./ProviderModelSelect";

const OPTIONS = ["gemini-aistudio/veo-3.1-generate-001", "ark/seedance"];
const PROVIDER_NAMES = { "gemini-aistudio": "Gemini AI Studio", ark: "Ark" };

describe("ProviderModelSelect – trigger display", () => {
  it("shows placeholder when value is empty and no fallback provided", () => {
    render(
      <ProviderModelSelect
        value=""
        options={OPTIONS}
        providerNames={PROVIDER_NAMES}
        onChange={() => {}}
      />,
    );
    expect(screen.getByRole("combobox")).toHaveTextContent(/选择模型/);
  });

  it("shows selected provider · model when value is non-empty", () => {
    render(
      <ProviderModelSelect
        value="ark/seedance"
        options={OPTIONS}
        providerNames={PROVIDER_NAMES}
        onChange={() => {}}
      />,
    );
    const trigger = screen.getByRole("combobox");
    expect(trigger).toHaveTextContent(/Ark/);
    expect(trigger).toHaveTextContent(/seedance/);
  });

  it("shows 'follow global default · provider · model' when value is empty and fallbackValue provided", () => {
    render(
      <ProviderModelSelect
        value=""
        options={OPTIONS}
        providerNames={PROVIDER_NAMES}
        onChange={() => {}}
        allowDefault
        fallbackValue="gemini-aistudio/veo-3.1-generate-001"
      />,
    );
    const trigger = screen.getByRole("combobox");
    expect(trigger).toHaveTextContent(/跟随全局默认/);
    expect(trigger).toHaveTextContent(/Gemini AI Studio/);
    expect(trigger).toHaveTextContent(/veo-3\.1-generate-001/);
  });

  it("prefers value over fallbackValue when both are provided", () => {
    render(
      <ProviderModelSelect
        value="ark/seedance"
        options={OPTIONS}
        providerNames={PROVIDER_NAMES}
        onChange={() => {}}
        allowDefault
        fallbackValue="gemini-aistudio/veo-3.1-generate-001"
      />,
    );
    const trigger = screen.getByRole("combobox");
    expect(trigger).not.toHaveTextContent(/跟随全局默认/);
    expect(trigger).toHaveTextContent(/Ark/);
    expect(trigger).toHaveTextContent(/seedance/);
  });
});
