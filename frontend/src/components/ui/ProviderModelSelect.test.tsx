import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi } from "vitest";
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

const MANY_OPTIONS = [
  "gemini-aistudio/veo-3.1-generate-001",
  "gemini-aistudio/veo-2.0-generate",
  "gemini-aistudio/imagen-4",
  "ark/seedance",
  "ark/seedream",
  "ark/jimeng",
  "openai/sora",
];
const MANY_PROVIDER_NAMES = {
  "gemini-aistudio": "Gemini AI Studio",
  ark: "火山方舟",
  openai: "OpenAI",
};

describe("ProviderModelSelect – search", () => {
  it("does not render search input when option count is below threshold", async () => {
    const user = userEvent.setup();
    render(
      <ProviderModelSelect
        value=""
        options={OPTIONS}
        providerNames={PROVIDER_NAMES}
        onChange={() => {}}
      />,
    );
    await user.click(screen.getByRole("combobox"));
    expect(screen.queryByPlaceholderText(/搜索模型或供应商/)).toBeNull();
  });

  it("renders search input when option count meets threshold", async () => {
    const user = userEvent.setup();
    render(
      <ProviderModelSelect
        value=""
        options={MANY_OPTIONS}
        providerNames={MANY_PROVIDER_NAMES}
        onChange={() => {}}
      />,
    );
    await user.click(screen.getByRole("combobox"));
    expect(screen.getByPlaceholderText(/搜索模型或供应商/)).toBeInTheDocument();
  });

  it("filters options by model name substring", async () => {
    const user = userEvent.setup();
    render(
      <ProviderModelSelect
        value=""
        options={MANY_OPTIONS}
        providerNames={MANY_PROVIDER_NAMES}
        onChange={() => {}}
      />,
    );
    await user.click(screen.getByRole("combobox"));
    await user.type(screen.getByPlaceholderText(/搜索模型或供应商/), "veo");
    const visible = screen.getAllByRole("option").map((el) => el.textContent ?? "");
    expect(visible).toHaveLength(2);
    expect(visible.every((text) => text.includes("veo"))).toBe(true);
  });

  it("matches provider display name and shows all of its models", async () => {
    const user = userEvent.setup();
    render(
      <ProviderModelSelect
        value=""
        options={MANY_OPTIONS}
        providerNames={MANY_PROVIDER_NAMES}
        onChange={() => {}}
      />,
    );
    await user.click(screen.getByRole("combobox"));
    await user.type(screen.getByPlaceholderText(/搜索模型或供应商/), "Gemini");
    const visible = screen.getAllByRole("option").map((el) => el.textContent ?? "");
    expect(visible).toHaveLength(3);
    expect(visible.some((text) => text.includes("veo-3.1-generate-001"))).toBe(true);
    expect(visible.some((text) => text.includes("veo-2.0-generate"))).toBe(true);
    expect(visible.some((text) => text.includes("imagen-4"))).toBe(true);
  });

  it("matches case-insensitively", async () => {
    const user = userEvent.setup();
    render(
      <ProviderModelSelect
        value=""
        options={MANY_OPTIONS}
        providerNames={MANY_PROVIDER_NAMES}
        onChange={() => {}}
      />,
    );
    await user.click(screen.getByRole("combobox"));
    await user.type(screen.getByPlaceholderText(/搜索模型或供应商/), "OPENAI");
    const visible = screen.getAllByRole("option").map((el) => el.textContent ?? "");
    expect(visible).toHaveLength(1);
    expect(visible[0]).toContain("sora");
  });

  it("shows empty state when nothing matches", async () => {
    const user = userEvent.setup();
    render(
      <ProviderModelSelect
        value=""
        options={MANY_OPTIONS}
        providerNames={MANY_PROVIDER_NAMES}
        onChange={() => {}}
      />,
    );
    await user.click(screen.getByRole("combobox"));
    await user.type(screen.getByPlaceholderText(/搜索模型或供应商/), "zzz-no-match");
    expect(screen.queryAllByRole("option")).toHaveLength(0);
    expect(screen.getByRole("status")).toHaveTextContent(/未找到匹配模型/);
  });

  it("hides default option while query is active", async () => {
    const user = userEvent.setup();
    render(
      <ProviderModelSelect
        value=""
        options={MANY_OPTIONS}
        providerNames={MANY_PROVIDER_NAMES}
        onChange={() => {}}
        allowDefault
      />,
    );
    await user.click(screen.getByRole("combobox"));
    expect(screen.getByText(/跟随全局默认/)).toBeInTheDocument();
    await user.type(screen.getByPlaceholderText(/搜索模型或供应商/), "veo");
    expect(screen.queryByText(/跟随全局默认/)).toBeNull();
  });

  it("does not render search input when searchable is false", async () => {
    const user = userEvent.setup();
    render(
      <ProviderModelSelect
        value=""
        options={MANY_OPTIONS}
        providerNames={MANY_PROVIDER_NAMES}
        onChange={() => {}}
        searchable={false}
      />,
    );
    await user.click(screen.getByRole("combobox"));
    expect(screen.queryByPlaceholderText(/搜索模型或供应商/)).toBeNull();
  });

  it("respects custom searchThreshold", async () => {
    const user = userEvent.setup();
    render(
      <ProviderModelSelect
        value=""
        options={OPTIONS}
        providerNames={PROVIDER_NAMES}
        onChange={() => {}}
        searchThreshold={2}
      />,
    );
    await user.click(screen.getByRole("combobox"));
    expect(screen.getByPlaceholderText(/搜索模型或供应商/)).toBeInTheDocument();
  });

  it("clears query when closing via trigger click", async () => {
    const user = userEvent.setup();
    render(
      <ProviderModelSelect
        value=""
        options={MANY_OPTIONS}
        providerNames={MANY_PROVIDER_NAMES}
        onChange={() => {}}
      />,
    );
    const trigger = screen.getByRole("combobox");
    await user.click(trigger);
    await user.type(screen.getByPlaceholderText(/搜索模型或供应商/), "sora");
    // Close by clicking the trigger again
    await user.click(trigger);
    // Reopen — search input should be empty
    await user.click(trigger);
    expect(screen.getByPlaceholderText(/搜索模型或供应商/)).toHaveValue("");
  });

  it("ignores Enter while IME composition is active", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      <ProviderModelSelect
        value=""
        options={MANY_OPTIONS}
        providerNames={MANY_PROVIDER_NAMES}
        onChange={onChange}
      />,
    );
    await user.click(screen.getByRole("combobox"));
    const input = screen.getByPlaceholderText(/搜索模型或供应商/);
    // Simulate IME composition: keydown with isComposing=true should not trigger select
    input.focus();
    const keydownEvent = new KeyboardEvent("keydown", {
      key: "Enter",
      bubbles: true,
      cancelable: true,
    });
    Object.defineProperty(keydownEvent, "isComposing", { value: true });
    input.dispatchEvent(keydownEvent);
    expect(onChange).not.toHaveBeenCalled();
  });

  it("uses unique ARIA ids for sibling instances on the same page", async () => {
    const user = userEvent.setup();
    render(
      <div>
        <ProviderModelSelect
          value=""
          options={MANY_OPTIONS}
          providerNames={MANY_PROVIDER_NAMES}
          onChange={() => {}}
          aria-label="first"
        />
        <ProviderModelSelect
          value=""
          options={MANY_OPTIONS}
          providerNames={MANY_PROVIDER_NAMES}
          onChange={() => {}}
          aria-label="second"
        />
      </div>,
    );
    const [first, second] = screen.getAllByRole("combobox");
    expect(first.getAttribute("aria-controls")).toBeTruthy();
    expect(first.getAttribute("aria-controls")).not.toBe(second.getAttribute("aria-controls"));

    // Open both and verify their listbox ids differ
    await user.click(first);
    const firstListbox = document.getElementById(first.getAttribute("aria-controls")!);
    expect(firstListbox).not.toBeNull();
    await user.click(second);
    const secondListbox = document.getElementById(second.getAttribute("aria-controls")!);
    expect(secondListbox).not.toBeNull();
    expect(firstListbox).not.toBe(secondListbox);
  });

  it("does not apply stale query filtering when search input is hidden", async () => {
    const user = userEvent.setup();
    // Start with searchable enabled so user can type a query
    const { rerender } = render(
      <ProviderModelSelect
        value=""
        options={MANY_OPTIONS}
        providerNames={MANY_PROVIDER_NAMES}
        onChange={() => {}}
      />,
    );
    await user.click(screen.getByRole("combobox"));
    await user.type(screen.getByPlaceholderText(/搜索模型或供应商/), "veo");
    expect(screen.getAllByRole("option")).toHaveLength(2);

    // Re-render with searchable disabled — search box hides; list must not
    // remain filtered by the now-invisible query.
    rerender(
      <ProviderModelSelect
        value=""
        options={MANY_OPTIONS}
        providerNames={MANY_PROVIDER_NAMES}
        onChange={() => {}}
        searchable={false}
      />,
    );
    expect(screen.queryByPlaceholderText(/搜索模型或供应商/)).toBeNull();
    expect(screen.getAllByRole("option")).toHaveLength(MANY_OPTIONS.length);
  });

  it("clears query when an option is selected", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      <ProviderModelSelect
        value=""
        options={MANY_OPTIONS}
        providerNames={MANY_PROVIDER_NAMES}
        onChange={onChange}
      />,
    );
    const trigger = screen.getByRole("combobox");
    await user.click(trigger);
    await user.type(screen.getByPlaceholderText(/搜索模型或供应商/), "sora");
    await user.click(screen.getByRole("option", { name: /sora/ }));
    expect(onChange).toHaveBeenCalledWith("openai/sora");
    // Reopen — search input should be empty again
    await user.click(trigger);
    expect(screen.getByPlaceholderText(/搜索模型或供应商/)).toHaveValue("");
  });
});
