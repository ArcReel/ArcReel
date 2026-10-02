// PROTOTYPE — 剧集页原型（#2974）：切换分镜时的草稿拦截与快捷键。
// 拦截对话框按「保存方式」结论：继续编辑 / 放弃修改 / 保存并切换；保存失败留在原处。
// 快捷键两套：J/K（输入框聚焦时不响应）或 Alt+↑/↓（输入框里也能用）；⌘S / Ctrl+S 都是保存当前分镜。

import { useEffect, useState } from "react";
import {
  AlertDialog,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Kbd } from "@/components/ui/kbd";
import { getShotGuard } from "./shotGuard";
import { useEpisodeProto } from "./store";

function isTyping(el: Element | null): boolean {
  if (!el) return false;
  if (el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement || el instanceof HTMLSelectElement) return true;
  return (el as HTMLElement).isContentEditable;
}

/** 返回受拦截的选择函数与需要渲染的对话框。 */
export function useShotSwitchGuard({
  selectedIndex,
  count,
  select,
  currentLabel,
}: {
  selectedIndex: number;
  count: number;
  select: (index: number) => void;
  currentLabel: string;
}) {
  const { axes } = useEpisodeProto();
  const mode = axes.shotNav;
  const [pending, setPending] = useState<number | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const guardedSelect = (index: number) => {
    if (index < 0 || index >= count || index === selectedIndex) return;
    if (mode !== "blocked" && getShotGuard()?.dirty) {
      setError(null);
      setPending(index);
      return;
    }
    select(index);
  };

  useEffect(() => {
    if (mode === "blocked") return;
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "s") {
        e.preventDefault();
        void getShotGuard()?.save();
        return;
      }
      if (pending !== null) return;
      if (mode === "intercept") {
        if (e.altKey || e.metaKey || e.ctrlKey || isTyping(document.activeElement)) return;
        if (e.key === "j" || e.key === "J") guardedSelect(selectedIndex + 1);
        if (e.key === "k" || e.key === "K") guardedSelect(selectedIndex - 1);
      } else if (e.altKey && (e.key === "ArrowDown" || e.key === "ArrowUp")) {
        e.preventDefault();
        guardedSelect(selectedIndex + (e.key === "ArrowDown" ? 1 : -1));
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  const close = () => {
    if (!saving) setPending(null);
  };

  const dialog = (
    <AlertDialog open={pending !== null} onOpenChange={(open) => !open && close()}>
      <AlertDialogContent className="data-[size=default]:sm:max-w-md">
        <AlertDialogHeader>
          <AlertDialogTitle>{currentLabel} 有未保存的修改</AlertDialogTitle>
          <AlertDialogDescription>切换到其他分镜前，先决定如何处理这些修改。</AlertDialogDescription>
        </AlertDialogHeader>
        {error && <p className="m-0 text-[12.5px] text-destructive">{error}</p>}
        <AlertDialogFooter>
          <Button variant="ghost" onClick={close} disabled={saving}>
            继续编辑
          </Button>
          <Button
            variant="outline"
            disabled={saving}
            onClick={() => {
              getShotGuard()?.discard();
              const target = pending;
              setPending(null);
              if (target !== null) select(target);
            }}
          >
            放弃修改
          </Button>
          <Button
            disabled={saving}
            onClick={async () => {
              setSaving(true);
              const ok = (await getShotGuard()?.save()) ?? true;
              setSaving(false);
              if (!ok) {
                setError("保存失败，修改仍保留在草稿中。");
                return;
              }
              const target = pending;
              setPending(null);
              if (target !== null) select(target);
            }}
          >
            {saving ? "保存中…" : "保存并切换"}
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );

  const hint =
    mode === "blocked" ? null : (
      <div className="flex shrink-0 flex-wrap items-center gap-x-2 gap-y-1 border-t border-[var(--color-hairline-soft)] px-3 py-2 text-[11px] text-muted-foreground">
        {mode === "intercept" ? (
          <span className="inline-flex items-center gap-1">
            <Kbd>J</Kbd>
            <Kbd>K</Kbd> 切换
          </span>
        ) : (
          <span className="inline-flex items-center gap-1">
            <Kbd>Alt</Kbd>
            <Kbd>↑</Kbd>
            <Kbd>↓</Kbd> 切换
          </span>
        )}
        <span className="inline-flex items-center gap-1">
          <Kbd>⌘</Kbd>
          <Kbd>S</Kbd> 保存
        </span>
      </div>
    );

  return { guardedSelect, dialog, hint };
}
