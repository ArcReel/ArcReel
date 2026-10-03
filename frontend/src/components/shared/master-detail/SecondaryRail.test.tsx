import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Router } from "wouter";
import { memoryLocation } from "wouter/memory-location";

import { SecondaryRail, type SecondaryRailGroup } from "./SecondaryRail";

const GROUPS: SecondaryRailGroup[] = [
  {
    id: "preset",
    label: "预置",
    items: [
      { id: "preset:a", label: "Alpha", icon: null, href: "/x?p=a" },
      { id: "preset:b", label: "Beta", icon: null, href: "/x?p=b" },
    ],
  },
  {
    id: "custom",
    label: "自定义",
    items: [{ id: "custom:1", label: "我的中转站", icon: null, href: "/x?c=1" }],
    action: { id: "custom:new", label: "添加自定义供应商", icon: null, href: "/x?c=new" },
  },
];

function renderRail(activeId: string) {
  const location = memoryLocation({ path: "/x" });
  const view = (id: string) => (
    <Router hook={location.hook} searchHook={location.searchHook}>
      <SecondaryRail label="供应商列表" groups={GROUPS} activeId={id} />
    </Router>
  );
  const result = render(view(activeId));
  return { ...result, select: (id: string) => result.rerender(view(id)) };
}

const activeTab = () => screen.getByRole("tab", { selected: true });

describe("SecondaryRail", () => {
  it("shows the group of the selected item until the user picks a tab, then keeps the user's choice", () => {
    const { select } = renderRail("custom:1");
    expect(activeTab()).toHaveTextContent("自定义");
    expect(within(screen.getByRole("tabpanel")).getByRole("link", { name: "我的中转站" })).toHaveAttribute(
      "aria-current",
      "page",
    );

    // 选中项换到另一组时，没手动切过的 Tab 跟过去
    select("preset:a");
    expect(activeTab()).toHaveTextContent("预置");

    fireEvent.click(screen.getByRole("tab", { name: /自定义/ }));
    select("preset:b");
    expect(activeTab()).toHaveTextContent("自定义");
  });

  it("counts only the group's items on each tab, not its trailing action", () => {
    renderRail("preset:a");
    expect(screen.getByRole("tab", { name: /预置/ })).toHaveTextContent("预置2");
    expect(screen.getByRole("tab", { name: /自定义/ })).toHaveTextContent("自定义1");
  });
});
