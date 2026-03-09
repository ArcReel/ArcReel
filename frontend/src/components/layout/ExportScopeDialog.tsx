import { Package, History } from "lucide-react";
import { Popover } from "@/components/ui/Popover";
import type { RefObject } from "react";

export type ExportScope = "current" | "full";

interface ExportScopeDialogProps {
  open: boolean;
  onClose: () => void;
  onSelect: (scope: ExportScope) => void;
  anchorRef: RefObject<HTMLElement | null>;
}

export function ExportScopeDialog({
  open,
  onClose,
  onSelect,
  anchorRef,
}: ExportScopeDialogProps) {
  return (
    <Popover
      open={open}
      onClose={onClose}
      anchorRef={anchorRef}
      width="w-72"
      className="rounded-lg border border-gray-700 p-3 shadow-xl"
    >
      <p className="mb-3 text-xs font-medium text-gray-300">选择导出范围</p>
      <div className="flex flex-col gap-2">
        <button
          type="button"
          onClick={() => onSelect("current")}
          className="flex items-start gap-3 rounded-md border border-gray-700 px-3 py-2.5 text-left transition-colors hover:border-indigo-500 hover:bg-indigo-500/10"
        >
          <Package className="mt-0.5 h-4 w-4 shrink-0 text-indigo-400" />
          <div>
            <div className="text-sm font-medium text-gray-200">
              仅当前版本
              <span className="ml-1.5 rounded bg-indigo-500/20 px-1.5 py-0.5 text-[10px] text-indigo-300">
                推荐
              </span>
            </div>
            <p className="mt-0.5 text-xs text-gray-500">
              不含版本历史，体积更小
            </p>
          </div>
        </button>
        <button
          type="button"
          onClick={() => onSelect("full")}
          className="flex items-start gap-3 rounded-md border border-gray-700 px-3 py-2.5 text-left transition-colors hover:border-gray-500 hover:bg-gray-800"
        >
          <History className="mt-0.5 h-4 w-4 shrink-0 text-gray-400" />
          <div>
            <div className="text-sm font-medium text-gray-200">全部数据</div>
            <p className="mt-0.5 text-xs text-gray-500">
              包含完整版本历史
            </p>
          </div>
        </button>
      </div>
    </Popover>
  );
}
