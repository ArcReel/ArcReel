import { KeyboardSensor, type KeyboardSensorProps } from "@dnd-kit/core";

const SORT_KEYS = new Set(["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "Space", "Enter", "Escape"]);

/** dnd-kit installs listeners and measures drop targets after pickup: buffer intervening keys until measurement completes, then replay them to the sensor in order. */
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
        // Dispatch directly to the sensor on document: Dialog / Sheet intercept arrow keys during React bubbling.
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
    // KeyboardSensor also installs document listeners through a zero-delay timer; replay only after that timer.
    const timer = setTimeout(() => { listening = true; }, 0);
  }
}
