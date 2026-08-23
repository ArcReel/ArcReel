import { vi } from "vitest";
import { API, type ProjectEventStreamOptions } from "@/api";

/**
 * Stubs `API.openProjectEventStream` so a test can drive the SSE callbacks
 * (onSnapshot/onChanges/onError/onProjectDeleted) directly, without a real
 * EventSource. `options` reflects whatever the hook under test most recently
 * registered.
 */
export function mockProjectEventStream() {
  let capturedOptions: ProjectEventStreamOptions | undefined;
  const close = vi.fn();
  const openSpy = vi.spyOn(API, "openProjectEventStream").mockImplementation((options) => {
    capturedOptions = options;
    return { close } as unknown as EventSource;
  });
  return {
    get options() {
      return capturedOptions;
    },
    close,
    openSpy,
  };
}
