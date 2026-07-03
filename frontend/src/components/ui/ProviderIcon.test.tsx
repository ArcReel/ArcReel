import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ProviderIcon } from "@/components/ui/ProviderIcon";

describe("ProviderIcon", () => {
  it("renders the Volcengine icon for the bare ark provider", () => {
    render(<ProviderIcon providerId="ark" />);
    expect(screen.getByTestId("lobehub-stub-icon")).toBeInTheDocument();
  });

  it("renders the Volcengine icon for ark-agent-plan, not the monogram fallback", () => {
    render(<ProviderIcon providerId="ark-agent-plan" />);
    expect(screen.getByTestId("lobehub-stub-icon")).toBeInTheDocument();
  });

  it("falls back to a monogram badge for an unknown provider", () => {
    render(<ProviderIcon providerId="zeta" />);
    expect(screen.queryByTestId("lobehub-stub-icon")).not.toBeInTheDocument();
    expect(screen.getByText("z")).toBeInTheDocument();
  });

  // 回归测试：守住已接线的内置 provider 图标不退化。
  // 注意这不是覆盖保证——图标库没有的内置 provider（如 Agnes）合法地走字母徽章，
  // 故本表只列「已接线」的 id，新增无图标 provider 不会、也不该让它变红。
  const WIRED_BUILTIN_PROVIDER_IDS = [
    "gemini-aistudio",
    "gemini-vertex",
    "grok",
    "ark",
    "ark-agent-plan",
    "dashscope",
    "minimax",
    "openai",
    "vidu",
    "kling",
  ];
  it.each(WIRED_BUILTIN_PROVIDER_IDS)("renders a brand icon for built-in provider %s", (providerId) => {
    render(<ProviderIcon providerId={providerId} />);
    expect(screen.getByTestId("lobehub-stub-icon")).toBeInTheDocument();
  });

  // 守住 Array.from 回退：首字符为星平面（非 BMP）字符时取整字，而非半个代理对。
  it("renders the full first Unicode character in the monogram fallback", () => {
    render(<ProviderIcon providerId="𠮷-provider" />);
    expect(screen.queryByTestId("lobehub-stub-icon")).not.toBeInTheDocument();
    expect(screen.getByText("𠮷")).toBeInTheDocument();
  });
});
