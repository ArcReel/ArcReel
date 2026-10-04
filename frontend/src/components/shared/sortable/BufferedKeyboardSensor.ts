import { KeyboardSensor, type KeyboardSensorProps } from "@dnd-kit/core";

const SORT_KEYS = new Set(["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "Space", "Enter", "Escape"]);

/** dnd-kit 的监听器和落点测量都晚于拿起：把这期间的按键留到测量完成，按原顺序交还传感器。 */
export class BufferedKeyboardSensor {
  static activators = KeyboardSensor.activators;
  autoScrollEnabled = false;

  constructor(props: KeyboardSensorProps) {
    const target = props.event.target as HTMLElement;
    const ownerWindow = target.ownerDocument.defaultView!;
    const pending: KeyboardEvent[] = [];
    let listening = false;
    let replaying = false;
    let frame: number | undefined;
    const ready = () => listening && props.context.current.collisionRect !== null && props.context.current.droppableRects.size > 0;
    const cleanup = () => {
      ownerWindow.removeEventListener("keydown", capture, true);
      if (frame !== undefined) ownerWindow.cancelAnimationFrame(frame);
      clearTimeout(timer);
      pending.length = 0;
    };
    const flush = () => {
      frame = undefined;
      if (!pending.length) return;
      if (ready()) {
        const event = pending.shift()!;
        replaying = true;
        // 直接交给 document 上的传感器：Dialog / Sheet 在 React 冒泡阶段会拦下方向键。
        target.ownerDocument.dispatchEvent(new KeyboardEvent("keydown", {
          key: event.key, code: event.code, bubbles: true, cancelable: true,
          altKey: event.altKey, ctrlKey: event.ctrlKey, metaKey: event.metaKey, shiftKey: event.shiftKey,
        }));
        replaying = false;
      }
      if (pending.length) frame = ownerWindow.requestAnimationFrame(flush);
    };
    const capture = (event: KeyboardEvent) => {
      if (replaying || !SORT_KEYS.has(event.code)) return;
      event.preventDefault();
      event.stopPropagation();
      pending.push(event);
      if (frame === undefined) frame = ownerWindow.requestAnimationFrame(flush);
    };
    ownerWindow.addEventListener("keydown", capture, true);
    new KeyboardSensor({
      ...props,
      onEnd: () => { cleanup(); props.onEnd(); },
      onCancel: () => { cleanup(); props.onCancel(); },
    });
    // KeyboardSensor 同样经零延迟 timer 安装 document 监听器；排在它之后才允许重放。
    const timer = setTimeout(() => { listening = true; }, 0);
  }
}
