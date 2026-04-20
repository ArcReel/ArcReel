import { useTranslation } from "react-i18next";
import { useEscapeClose } from "@/hooks/useEscapeClose";

export type ConflictResolution = "replace" | "rename" | "cancel";

interface ConflictModalProps {
  existing: string;
  suggestedName: string;
  onResolve: (decision: ConflictResolution) => void;
}

export function ConflictModal({ existing, suggestedName, onResolve }: ConflictModalProps) {
  const { t } = useTranslation("common");
  useEscapeClose(() => onResolve("cancel"));
  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="conflict-modal-title"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
    >
      <div className="w-full max-w-md rounded-xl border border-gray-700 bg-gray-900 p-6 shadow-xl">
        <h2 id="conflict-modal-title" className="text-lg font-semibold text-gray-100">
          {t("conflict_modal_title")}
        </h2>
        <p className="mt-2 text-sm text-gray-400">
          {t("conflict_modal_desc", { filename: existing })}
        </p>
        <p className="mt-3 text-xs text-gray-500">
          {`→ ${suggestedName}`}
        </p>
        <div className="mt-5 flex justify-end gap-2">
          <button
            type="button"
            onClick={() => onResolve("cancel")}
            className="rounded-md border border-gray-700 px-3 py-1.5 text-sm text-gray-300 hover:bg-gray-800"
          >
            {t("cancel")}
          </button>
          <button
            type="button"
            onClick={() => onResolve("rename")}
            className="rounded-md border border-gray-700 px-3 py-1.5 text-sm text-gray-200 hover:bg-gray-800"
          >
            {t("keep_both")}
          </button>
          <button
            type="button"
            onClick={() => onResolve("replace")}
            className="rounded-md bg-indigo-600 px-3 py-1.5 text-sm text-white hover:bg-indigo-500"
          >
            {t("replace")}
          </button>
        </div>
      </div>
    </div>
  );
}
