import { vi } from "vitest";

/**
 * jsdom HTMLMediaElement lacks playback: play/pause/load raise "Not implemented".
 * Call before rendering `<video>`/`<audio>` players, replacing methods uniformly with side-effect-free stubs;
 * add newly used methods here only. Return spies for target assertions through `mock.contexts`.
 * Restore through `vi.restoreAllMocks()`.
 */
export function stubMediaElementPlayback() {
  return {
    play: vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue(),
    pause: vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => undefined),
    load: vi.spyOn(HTMLMediaElement.prototype, "load").mockImplementation(() => undefined),
  };
}
