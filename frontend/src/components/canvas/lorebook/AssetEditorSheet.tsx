import { useState, type ReactNode } from "react";
import { Sheet, SheetBody, SheetContent, SheetHeader, SheetTitle } from "@/components/ui/sheet";

/**
 * 点浏览卡打开的右侧详情 Sheet，约 560px 宽、覆盖全视口，可以盖住 Agent 面板。
 * 过渡实现：正文沿用各类资产原来的编辑卡片，字段统一为编辑单元前不拦截未保存的修改。
 */
export function AssetEditorSheet({
  name,
  onClose,
  children,
}: {
  /** 为 null 时关闭。 */
  name: string | null;
  onClose: () => void;
  /** 由当前显示的资产名渲染正文。 */
  children: (name: string) => ReactNode;
}) {
  // 关闭动画期间沿用上一次的资产
  const [shown, setShown] = useState(name);
  if (name !== null && name !== shown) setShown(name);

  return (
    <Sheet
      open={name !== null}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <SheetContent side="right" className="data-[side=right]:w-140">
        <SheetHeader>
          <SheetTitle>{shown}</SheetTitle>
        </SheetHeader>
        <SheetBody>{shown !== null && children(shown)}</SheetBody>
      </SheetContent>
    </Sheet>
  );
}
