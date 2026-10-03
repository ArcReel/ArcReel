import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";
import { Router } from "wouter";
import { memoryLocation } from "wouter/memory-location";

import { useReturnTo, useTrackReturnTo } from "./return-to";

/** 路由根部记录停留的页面，「返回」按钮与全局设置、资产库里的一致。 */
function App() {
  useTrackReturnTo();
  const goBack = useReturnTo();
  return (
    <button type="button" onClick={goBack}>
      返回
    </button>
  );
}

function renderAt(path: string) {
  const location = memoryLocation({ path, record: true });
  render(
    <Router hook={location.hook}>
      <App />
    </Router>,
  );
  return location;
}

describe("返回进入之前的页面", () => {
  beforeEach(() => {
    sessionStorage.clear();
  });

  it("从工作台进入设置，切换分区后返回仍回到工作台原来的位置", async () => {
    const user = userEvent.setup();
    const { navigate, history } = renderAt("/app/projects/demo/episodes/1?view=board");

    act(() => navigate("/app/settings?section=usage"));
    act(() => navigate("/app/settings?section=general", { replace: true }));
    await user.click(screen.getByRole("button", { name: "返回" }));

    expect(history.at(-1)).toBe("/app/projects/demo/episodes/1?view=board");
  });

  it("先去过工作台、再从大厅进入设置，返回回到大厅", async () => {
    const user = userEvent.setup();
    const { navigate, history } = renderAt("/app/projects/demo");

    act(() => navigate("/app/projects"));
    act(() => navigate("/app/settings"));
    await user.click(screen.getByRole("button", { name: "返回" }));

    expect(history.at(-1)).toBe("/app/projects");
  });

  it("直接打开设置时返回项目大厅", async () => {
    const user = userEvent.setup();
    const { history } = renderAt("/app/settings?section=about");

    await user.click(screen.getByRole("button", { name: "返回" }));

    expect(history.at(-1)).toBe("/app/projects");
  });

  it("只做重定向的演示项目设置地址不作为返回目标", async () => {
    const user = userEvent.setup();
    const { navigate, history } = renderAt("/app/projects");

    act(() => navigate("/app/projects/onboarding_demo/settings"));
    act(() => navigate("/app/settings", { replace: true }));
    await user.click(screen.getByRole("button", { name: "返回" }));

    expect(history.at(-1)).toBe("/app/projects");
  });
});
