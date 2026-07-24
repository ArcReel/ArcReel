// PROTOTYPE — throwaway code for issue #1276 (尾帧设置 UI 交互原型)。
// 选图器弹窗：项目内图片分组浏览 + 上传任意图片两条通道。数据为 mock。
import { useId, useRef, useState } from "react";
import { Check, ImagePlus, Upload } from "lucide-react";
import { GlassModal } from "@/components/ui/GlassModal";
import { ModalCloseButton } from "@/components/ui/ModalCloseButton";
import { PrimaryButton } from "@/components/ui/PrimaryButton";
import { SecondaryButton } from "@/components/ui/SecondaryButton";

/** mock 图片：渐变色块代替真图，label 表意来源 */
export interface MockImage {
  id: string;
  label: string;
  gradient: string;
  group: string;
}

const g = (h1: number, h2: number) =>
  `linear-gradient(135deg, oklch(0.45 0.10 ${h1}), oklch(0.28 0.08 ${h2}))`;

export const MOCK_GROUPS: { title: string; images: MockImage[] }[] = [
  {
    title: "本集分镜图",
    images: Array.from({ length: 6 }, (_, i) => ({
      id: `sb-${i + 1}`,
      label: `E1S0${i + 1} 分镜图`,
      gradient: g(200 + i * 25, 260 + i * 20),
      group: "本集分镜图",
    })),
  },
  {
    title: "角色",
    images: [
      { id: "ch-1", label: "林澈", gradient: g(30, 60), group: "角色" },
      { id: "ch-2", label: "沈青梧", gradient: g(330, 300), group: "角色" },
      { id: "ch-3", label: "老掌柜", gradient: g(80, 110), group: "角色" },
    ],
  },
  {
    title: "场景",
    images: [
      { id: "sc-1", label: "药铺内堂", gradient: g(140, 170), group: "场景" },
      { id: "sc-2", label: "雨夜长街", gradient: g(240, 270), group: "场景" },
    ],
  },
  {
    title: "宫格切图",
    images: Array.from({ length: 4 }, (_, i) => ({
      id: `gr-${i + 1}`,
      label: `九宫格 #${i + 1}`,
      gradient: g(180 + i * 40, 220 + i * 40),
      group: "宫格切图",
    })),
  },
];

interface EndFramePickerProps {
  onClose: () => void;
  onSelect: (img: MockImage) => void;
}

/**
 * 尾帧选图器：单选。
 * 通道一：项目内图片按来源分组平铺浏览；通道二：header 的「上传图片」按钮。
 * 上传后直接作为选中项确认（模拟）。
 */
export function EndFramePicker({ onClose, onSelect }: EndFramePickerProps) {
  const titleId = useId();
  const [selected, setSelected] = useState<MockImage | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const confirmUpload = (file: File) => {
    onSelect({
      id: `upload-${file.name}`,
      label: file.name,
      gradient: g(55, 25),
      group: "已上传",
    });
  };

  return (
    <GlassModal
      open
      onClose={onClose}
      labelledBy={titleId}
      widthClassName="w-[720px] max-w-[96vw]"
      panelClassName="flex max-h-[85vh] flex-col"
    >
      {/* Header */}
      <div
        className="flex items-center gap-3 px-5 py-4"
        style={{ borderBottom: "1px solid var(--color-hairline-soft)" }}
      >
        <span
          aria-hidden
          className="grid h-9 w-9 shrink-0 place-items-center rounded-lg"
          style={{
            background:
              "linear-gradient(135deg, var(--color-accent-dim), oklch(0.76 0.09 295 / 0.05))",
            border: "1px solid var(--color-accent-soft)",
            color: "var(--color-accent-2)",
            boxShadow: "0 8px 18px -8px var(--color-accent-glow)",
          }}
        >
          <ImagePlus className="h-4 w-4" />
        </span>
        <div className="min-w-0 flex-1">
          <h3
            id={titleId}
            className="display-serif truncate text-[15px] font-semibold tracking-tight"
            style={{ color: "var(--color-text)" }}
          >
            选择尾帧图片
          </h3>
          <div
            className="num text-[10px] uppercase"
            style={{ color: "var(--color-text-4)", letterSpacing: "1.0px" }}
          >
            视频将从分镜图过渡到这张图片
          </div>
        </div>

        <input
          ref={fileRef}
          type="file"
          accept=".png,.jpg,.jpeg,.webp"
          className="hidden"
          onChange={(e) => {
            const f = e.target.files?.[0];
            e.target.value = "";
            if (f) confirmUpload(f);
          }}
        />
        <SecondaryButton size="sm" onClick={() => fileRef.current?.click()}>
          <Upload className="h-3.5 w-3.5" />
          <span>上传图片</span>
        </SecondaryButton>
        <ModalCloseButton onClick={onClose} />
      </div>

      {/* Grouped grid */}
      <div className="flex-1 overflow-y-auto p-4">
        {MOCK_GROUPS.map((grp) => (
          <div key={grp.title} className="mb-4 last:mb-0">
            <div
              className="num mb-2 text-[10px] font-bold uppercase"
              style={{ color: "var(--color-text-3)", letterSpacing: "1.2px" }}
            >
              {grp.title}
              <span className="ml-1.5" style={{ color: "var(--color-text-4)" }}>
                {grp.images.length}
              </span>
            </div>
            <div className="grid grid-cols-5 gap-2">
              {grp.images.map((img) => {
                const sel = selected?.id === img.id;
                return (
                  <button
                    key={img.id}
                    type="button"
                    aria-pressed={sel}
                    onClick={() => setSelected(sel ? null : img)}
                    className="focus-ring relative overflow-hidden rounded-lg text-left transition-transform hover:-translate-y-px"
                    style={{
                      border: sel
                        ? "1px solid var(--color-accent-soft)"
                        : "1px solid var(--color-hairline)",
                      boxShadow: sel
                        ? "0 6px 18px -6px var(--color-accent-glow)"
                        : "inset 0 1px 0 oklch(1 0 0 / 0.03)",
                    }}
                  >
                    <div className="aspect-[9/16] w-full" style={{ background: img.gradient }} />
                    <div
                      className="truncate px-1.5 py-1 text-[10.5px] font-medium"
                      style={{
                        color: "var(--color-text-2)",
                        background: "oklch(0.16 0.010 265 / 0.85)",
                      }}
                    >
                      {img.label}
                    </div>
                    {sel && (
                      <span
                        aria-hidden
                        className="absolute right-1.5 top-1.5 grid h-5 w-5 place-items-center rounded-full"
                        style={{
                          color: "oklch(0.14 0 0)",
                          background:
                            "linear-gradient(135deg, var(--color-accent-2), var(--color-accent))",
                          boxShadow:
                            "inset 0 1px 0 oklch(1 0 0 / 0.35), 0 0 0 1px var(--color-accent-soft)",
                        }}
                      >
                        <Check className="h-3 w-3" strokeWidth={3} />
                      </span>
                    )}
                  </button>
                );
              })}
            </div>
          </div>
        ))}
      </div>

      {/* Footer */}
      <div
        className="flex items-center gap-2 px-5 py-3"
        style={{ borderTop: "1px solid var(--color-hairline-soft)" }}
      >
        <span className="num flex-1 text-[11px]" style={{ color: "var(--color-text-4)" }}>
          {selected ? `已选：${selected.label}` : "从项目图片中选一张，或上传图片"}
        </span>
        <SecondaryButton size="sm" onClick={onClose}>
          取消
        </SecondaryButton>
        <PrimaryButton size="sm" disabled={!selected} onClick={() => selected && onSelect(selected)}>
          设为尾帧
        </PrimaryButton>
      </div>
    </GlassModal>
  );
}
