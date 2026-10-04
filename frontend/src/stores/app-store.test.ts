import { beforeEach, describe, expect, it, vi } from "vitest";

/** 模拟刷新页面：重新加载模块，store 从 localStorage 重新读取开合记忆。 */
async function reloadStore() {
  vi.resetModules();
  const { useAppStore } = await import("./app-store");
  return useAppStore;
}

describe("assistant panel state", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("opens by default when no choice is remembered", async () => {
    const store = await reloadStore();

    expect(store.getState().assistantPanelOpen).toBe(true);
  });

  it.each([
    [true, false],
    [false, true],
  ])("keeps a manual %s → %s choice across page reloads", async (initialOpen, expectedOpen) => {
    const store = await reloadStore();
    store.setState({ assistantPanelOpen: initialOpen });
    store.getState().toggleAssistantPanel();

    const reloaded = await reloadStore();

    expect(reloaded.getState().assistantPanelOpen).toBe(expectedOpen);
  });

  it("does not remember a programmatic expansion", async () => {
    const store = await reloadStore();
    store.getState().toggleAssistantPanel();
    expect(store.getState().assistantPanelOpen).toBe(false);

    store.getState().setAssistantPanelOpen(true);
    expect(store.getState().assistantPanelOpen).toBe(true);

    const reloaded = await reloadStore();

    expect(reloaded.getState().assistantPanelOpen).toBe(false);
  });
});
