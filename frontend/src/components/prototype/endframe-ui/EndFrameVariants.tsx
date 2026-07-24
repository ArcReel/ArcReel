// PROTOTYPE — throwaway code for issue #1276 (尾帧设置 UI 交互原型)。
// 三个结构性不同的尾帧设置入口变体，?efproto=a|b|c 切换：
//   a — 胶片过渡条：视频卡上方「首帧 → 尾帧」时间轴条带
//   b — 卡头图标：video 卡头 icon 入口 + 预览区角标，零垂直占位
//   c — 可展开设置行：视频卡上方表单式折叠行
import { useState, type ReactNode } from "react";
import {
  ArrowRight,
  ChevronRight,
  ImageDown,
  Lock,
  Plus,
  X,
} from "lucide-react";
import { PrototypeBar, PROTOTYPE_ENABLED, usePrototypeVariant } from "./PrototypeBar";
import { EndFramePicker, type MockImage } from "./EndFramePicker";

const UNSUPPORTED_HINT = "当前视频模型不支持尾帧，请在设置中更换模型或调整能力覆盖";

interface EndFramePrototype {
  /** 渲染在 video MediaCard 上方（变体 a / c 的入口） */
  aboveVideo: ReactNode;
  /** 注入 video MediaCard 卡头（变体 b 的 icon 入口） */
  headerExtra: ReactNode;
  /** 叠加在 video 预览区（变体 b 的已设置角标） */
  mediaOverlay: ReactNode;
  /** 浮动切换条 + 选图器弹窗，渲染一次即可 */
  chrome: ReactNode;
}

/**
 * ShotDetail 内调用；返回各挂载点的节点。原型状态（已选尾帧、
 * 模拟能力开关）保存在本 hook，不落盘。
 */
export function useEndFramePrototype(storyboardUrl: string | null): EndFramePrototype {
  const variant = usePrototypeVariant("efproto", ["a", "b", "c"]);
  const [endFrame, setEndFrame] = useState<MockImage | null>(null);
  const [supported, setSupported] = useState(true);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [expanded, setExpanded] = useState(false);

  if (!PROTOTYPE_ENABLED)
    return { aboveVideo: null, headerExtra: null, mediaOverlay: null, chrome: null };

  const openPicker = () => supported && setPickerOpen(true);
  const clear = () => setEndFrame(null);

  const chrome = (
    <>
      <PrototypeBar
        param="efproto"
        variants={["a", "b", "c"]}
        labels={{ a: "胶片过渡条", b: "卡头图标", c: "可展开设置行" }}
        extra={
          <button
            type="button"
            onClick={() => setSupported((s) => !s)}
            className="focus-ring rounded-full px-2 py-0.5 text-[10.5px] font-semibold transition-colors"
            style={{
              color: supported ? "oklch(0.8 0.15 150)" : "oklch(0.75 0.12 30)",
              background: supported ? "oklch(0.5 0.12 150 / 0.2)" : "oklch(0.5 0.12 30 / 0.2)",
              border: `1px solid ${supported ? "oklch(0.6 0.12 150 / 0.4)" : "oklch(0.6 0.12 30 / 0.4)"}`,
            }}
            title="模拟：当前视频模型是否支持尾帧（切换查看门控禁用态）"
          >
            模型支持尾帧：{supported ? "是" : "否"}
          </button>
        }
      />
      {pickerOpen && (
        <EndFramePicker
          onClose={() => setPickerOpen(false)}
          onSelect={(img) => {
            setEndFrame(img);
            setPickerOpen(false);
          }}
        />
      )}
    </>
  );

  /* ---------- 变体 A：胶片过渡条 ---------- */
  const thumbCls = "h-16 w-9 shrink-0 overflow-hidden rounded-[6px]";
  const variantA = (
    <div
      className="flex items-center gap-3 rounded-[10px] px-3 py-2.5"
      title={supported ? undefined : UNSUPPORTED_HINT}
      style={{
        border: "1px solid var(--color-hairline)",
        background: "oklch(0.18 0.010 265 / 0.4)",
        opacity: supported ? 1 : 0.45,
      }}
    >
      {/* 首帧：恒为分镜图，锁定 */}
      <div className="flex flex-col items-center gap-1">
        <div className={thumbCls} style={{ border: "1px solid var(--color-hairline)" }}>
          {storyboardUrl ? (
            <img src={storyboardUrl} alt="首帧（分镜图）" className="h-full w-full object-cover" />
          ) : (
            <div
              className="grid h-full w-full place-items-center"
              style={{ background: "oklch(0.22 0.011 265)", color: "var(--color-text-4)" }}
            >
              <Lock className="h-3 w-3" />
            </div>
          )}
        </div>
        <span className="num flex items-center gap-0.5 text-[9px]" style={{ color: "var(--color-text-4)" }}>
          <Lock className="h-2.5 w-2.5" />
          首帧·分镜图
        </span>
      </div>

      <div className="flex flex-1 flex-col items-center gap-0.5">
        <div
          className="h-px w-full"
          style={{
            background:
              "repeating-linear-gradient(90deg, var(--color-hairline-strong) 0 6px, transparent 6px 12px)",
          }}
        />
        <span className="flex items-center gap-1 text-[10px]" style={{ color: "var(--color-text-3)" }}>
          视频过渡
          <ArrowRight className="h-3 w-3" />
        </span>
      </div>

      {/* 尾帧槽 */}
      <div className="flex flex-col items-center gap-1">
        {endFrame ? (
          <div className="group relative">
            <button
              type="button"
              onClick={openPicker}
              disabled={!supported}
              title="更换尾帧"
              className={`${thumbCls} focus-ring block disabled:cursor-not-allowed`}
              style={{
                border: "1px solid var(--color-accent-soft)",
                background: endFrame.gradient,
                boxShadow: "0 4px 12px -4px var(--color-accent-glow)",
              }}
            />
            <button
              type="button"
              onClick={clear}
              aria-label="清除尾帧"
              className="focus-ring absolute -right-1.5 -top-1.5 grid h-4 w-4 place-items-center rounded-full opacity-0 transition-opacity group-hover:opacity-100 focus-visible:opacity-100"
              style={{
                background: "oklch(0.3 0.05 30)",
                color: "oklch(0.9 0.02 30)",
                border: "1px solid oklch(0.5 0.1 30 / 0.5)",
              }}
            >
              <X className="h-2.5 w-2.5" />
            </button>
          </div>
        ) : (
          <button
            type="button"
            onClick={openPicker}
            disabled={!supported}
            title={supported ? "设置尾帧" : UNSUPPORTED_HINT}
            className={`${thumbCls} focus-ring grid place-items-center disabled:cursor-not-allowed`}
            style={{
              border: "1px dashed var(--color-hairline-strong)",
              background: "oklch(0.20 0.011 265 / 0.5)",
              color: "var(--color-text-3)",
            }}
          >
            <Plus className="h-3.5 w-3.5" />
          </button>
        )}
        <span
          className="num max-w-[72px] truncate text-[9px]"
          style={{ color: endFrame ? "var(--color-accent-2)" : "var(--color-text-4)" }}
        >
          {endFrame ? endFrame.label : "尾帧·可选"}
        </span>
      </div>
    </div>
  );

  /* ---------- 变体 B：卡头图标 + 预览角标 ---------- */
  const headerExtraB = (
    <button
      type="button"
      onClick={openPicker}
      disabled={!supported}
      title={supported ? (endFrame ? `尾帧：${endFrame.label}（点击更换）` : "设置尾帧") : UNSUPPORTED_HINT}
      aria-label="设置尾帧"
      className="focus-ring relative inline-flex h-7 w-7 items-center justify-center rounded-md transition-colors hover:bg-[oklch(1_0_0_/_0.05)] disabled:cursor-not-allowed disabled:opacity-50"
      style={{ color: endFrame ? "var(--color-accent-2)" : "var(--color-text-3)" }}
    >
      <ImageDown className="h-3.5 w-3.5" />
      {endFrame && (
        <span
          aria-hidden
          className="absolute right-1 top-1 h-1.5 w-1.5 rounded-full"
          style={{
            background: "var(--color-accent)",
            boxShadow: "0 0 4px var(--color-accent-glow)",
          }}
        />
      )}
    </button>
  );

  const mediaOverlayB = endFrame ? (
    <div className="group absolute right-2 top-2 z-10">
      <div
        className="w-10 overflow-hidden rounded-[6px]"
        style={{
          border: "1px solid var(--color-accent-soft)",
          boxShadow: "0 4px 12px -4px oklch(0 0 0 / 0.6)",
        }}
      >
        <div className="aspect-[9/16] w-full" style={{ background: endFrame.gradient }} />
        <div
          className="num px-1 py-0.5 text-center text-[8px] uppercase"
          style={{
            background: "oklch(0.14 0.01 265 / 0.9)",
            color: "var(--color-accent-2)",
            letterSpacing: "0.5px",
          }}
        >
          尾帧
        </div>
      </div>
      <button
        type="button"
        onClick={clear}
        aria-label="清除尾帧"
        className="focus-ring absolute -right-1.5 -top-1.5 grid h-4 w-4 place-items-center rounded-full opacity-0 transition-opacity group-hover:opacity-100 focus-visible:opacity-100"
        style={{
          background: "oklch(0.3 0.05 30)",
          color: "oklch(0.9 0.02 30)",
          border: "1px solid oklch(0.5 0.1 30 / 0.5)",
        }}
      >
        <X className="h-2.5 w-2.5" />
      </button>
    </div>
  ) : null;

  /* ---------- 变体 C：可展开设置行 ---------- */
  const variantC = (
    <div
      className="rounded-[10px]"
      style={{
        border: "1px solid var(--color-hairline)",
        background: "oklch(0.18 0.010 265 / 0.4)",
      }}
    >
      <button
        type="button"
        onClick={() => setExpanded((e) => !e)}
        aria-expanded={expanded}
        className="focus-ring flex w-full items-center gap-2 px-3 py-2 text-left"
      >
        <ChevronRight
          className="h-3.5 w-3.5 transition-transform"
          style={{
            color: "var(--color-text-3)",
            transform: expanded ? "rotate(90deg)" : undefined,
          }}
        />
        <span className="text-[12px] font-semibold" style={{ color: "var(--color-text-2)" }}>
          尾帧
        </span>
        <span className="flex-1" />
        {endFrame ? (
          <span className="flex items-center gap-1.5">
            <span
              aria-hidden
              className="h-4 w-2.5 rounded-[3px]"
              style={{ background: endFrame.gradient, border: "1px solid var(--color-accent-soft)" }}
            />
            <span className="num max-w-[140px] truncate text-[11px]" style={{ color: "var(--color-accent-2)" }}>
              {endFrame.label}
            </span>
          </span>
        ) : (
          <span className="num text-[11px]" style={{ color: "var(--color-text-4)" }}>
            {supported ? "未设置" : "模型不支持"}
          </span>
        )}
      </button>
      {expanded && (
        <div
          className="flex items-start gap-3 px-3 pb-3 pt-1"
          style={{ borderTop: "1px solid var(--color-hairline-soft)" }}
        >
          {supported ? (
            <>
              <div
                className="h-28 w-16 shrink-0 overflow-hidden rounded-[6px]"
                style={{
                  border: endFrame
                    ? "1px solid var(--color-accent-soft)"
                    : "1px dashed var(--color-hairline-strong)",
                  background: endFrame ? endFrame.gradient : "oklch(0.20 0.011 265 / 0.5)",
                }}
              >
                {!endFrame && (
                  <div
                    className="grid h-full w-full place-items-center text-[9.5px]"
                    style={{ color: "var(--color-text-4)" }}
                  >
                    未设置
                  </div>
                )}
              </div>
              <div className="flex flex-1 flex-col gap-2 pt-1">
                <p className="text-[11px] leading-relaxed" style={{ color: "var(--color-text-3)" }}>
                  设置后视频以分镜图开场、过渡到这张图片收尾；重新生成会沿用。
                </p>
                <div className="flex gap-2">
                  <button
                    type="button"
                    onClick={openPicker}
                    className="focus-ring rounded-md px-2.5 py-1 text-[11.5px] font-medium transition-colors hover:bg-[oklch(0.26_0.013_265_/_0.7)]"
                    style={{
                      border: "1px solid var(--color-hairline)",
                      background: "oklch(0.22 0.011 265 / 0.5)",
                      color: "var(--color-text-2)",
                    }}
                  >
                    {endFrame ? "更换图片" : "选择图片"}
                  </button>
                  {endFrame && (
                    <button
                      type="button"
                      onClick={clear}
                      className="focus-ring rounded-md px-2.5 py-1 text-[11.5px] transition-colors hover:bg-[oklch(0.26_0.013_265_/_0.7)]"
                      style={{ color: "var(--color-text-3)" }}
                    >
                      清除
                    </button>
                  )}
                </div>
              </div>
            </>
          ) : (
            <p className="pt-1 text-[11px] leading-relaxed" style={{ color: "var(--color-text-4)" }}>
              {UNSUPPORTED_HINT}
            </p>
          )}
        </div>
      )}
    </div>
  );

  return {
    aboveVideo: variant === "a" ? variantA : variant === "c" ? variantC : null,
    headerExtra: variant === "b" ? headerExtraB : null,
    mediaOverlay: variant === "b" ? mediaOverlayB : null,
    chrome,
  };
}
