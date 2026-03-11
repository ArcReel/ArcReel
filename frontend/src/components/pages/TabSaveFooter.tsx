import { Loader2, Save } from "lucide-react";

interface TabSaveFooterProps {
  isDirty: boolean;
  saving: boolean;
  error: string | null;
  onSave: () => void;
  onReset: () => void;
}

/**
 * 配置 Tab 底部保存页脚。
 * - isDirty=false: 正常嵌入，保存按钮禁用
 * - isDirty=true:  sticky 固定在视口底部，保存按钮高亮
 */
export function TabSaveFooter({ isDirty, saving, error, onSave, onReset }: TabSaveFooterProps) {
  return (
    <div
      className={`border-t border-gray-800 bg-gray-950 px-4 py-3 flex items-center justify-between${
        isDirty ? " sticky bottom-0 z-10 shadow-[0_-2px_8px_rgba(0,0,0,0.18)]" : ""
      }`}
    >
      <div className="flex items-center gap-3 min-w-0">
        {isDirty && !error && (
          <span className="text-sm text-gray-400">有未保存的更改</span>
        )}
        {error && (
          <span className="text-sm text-rose-400 truncate">{error}</span>
        )}
      </div>
      <div className="flex items-center gap-2 shrink-0">
        {isDirty && (
          <button
            type="button"
            onClick={onReset}
            disabled={saving}
            className="inline-flex items-center gap-2 rounded-lg border border-gray-700 bg-gray-900 px-3 py-2 text-sm text-gray-200 transition-colors hover:border-gray-600 hover:bg-gray-800/80 disabled:cursor-not-allowed disabled:opacity-60"
          >
            撤销
          </button>
        )}
        <button
          type="button"
          onClick={onSave}
          disabled={!isDirty || saving}
          className={`inline-flex items-center gap-2 rounded-lg px-4 py-2 text-sm font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-60 ${
            isDirty
              ? "bg-indigo-600 text-white hover:bg-indigo-500"
              : "bg-gray-800 text-gray-500"
          }`}
        >
          {saving ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <Save className="h-4 w-4" />
          )}
          {saving ? "保存中…" : "保存"}
        </button>
      </div>
    </div>
  );
}
