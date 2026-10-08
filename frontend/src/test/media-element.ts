import { vi } from "vitest";

/**
 * jsdom 的 HTMLMediaElement 不实现播放：play/pause/load 会抛 "Not implemented"。
 * 渲染带 `<video>`/`<audio>` 播放器的组件前调用，统一替换为无副作用的桩；
 * 播放器用到新的媒体元素方法时只在这里补。返回各个 spy，供断言调用对象（`mock.contexts`）。
 * 随 `vi.restoreAllMocks()` 还原。
 */
export function stubMediaElementPlayback() {
  return {
    play: vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue(),
    pause: vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => undefined),
    load: vi.spyOn(HTMLMediaElement.prototype, "load").mockImplementation(() => undefined),
  };
}
