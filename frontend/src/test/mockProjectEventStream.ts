import { vi } from "vitest";
import { API, type ProjectEventStreamOptions } from "@/api";
import { FakeEventSource } from "./fakeEventSource";

/**
 * 把 `API.openProjectEventStream` 打桩为返回 {@link FakeEventSource} 实例的 spy：
 * 测试直接驱动 `options` 上的回调（onSnapshot/onChanges/onError/onProjectDeleted），
 * 不经真实 EventSource。`options` 反映被测 hook 最近一次注册的那组回调。
 */
export function mockProjectEventStream() {
  let capturedOptions: ProjectEventStreamOptions | undefined;
  FakeEventSource.reset();
  const openSpy = vi.spyOn(API, "openProjectEventStream").mockImplementation((options) => {
    capturedOptions = options;
    return new FakeEventSource() as unknown as EventSource;
  });
  return {
    get options() {
      return capturedOptions;
    },
    /** 最近一次建立的连接；断言 close 次数时用它的 `close`。 */
    get source() {
      return FakeEventSource.instances[FakeEventSource.instances.length - 1];
    },
    get close() {
      return this.source.close;
    },
    openSpy,
  };
}
