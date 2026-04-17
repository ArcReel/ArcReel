import { useTranslation } from "react-i18next";
import { Plus, Package } from "lucide-react";

interface Props {
  title: string;
  count: number;
  onAdd: () => void;
  onPickFromLibrary: () => void;
}

export function GalleryToolbar({ title, count, onAdd, onPickFromLibrary }: Props) {
  const { t } = useTranslation(["dashboard", "assets"]);
  return (
    <div className="flex items-center gap-3 px-4 py-3 border-b border-gray-800 bg-gray-900/60">
      <h2 className="text-sm font-semibold text-white">{title}</h2>
      <span className="text-xs px-2 py-0.5 rounded-full bg-gray-800 text-gray-400">{count}</span>
      <div className="flex-1" />
      <button onClick={onPickFromLibrary}
        className="flex items-center gap-1 px-3 py-1.5 text-xs text-indigo-300 border border-indigo-700 rounded hover:bg-indigo-950">
        <Package className="h-3.5 w-3.5" />
        {t("assets:from_library")}
      </button>
      <button onClick={onAdd}
        className="flex items-center gap-1 px-3 py-1.5 text-xs text-white bg-indigo-600 rounded hover:bg-indigo-500">
        <Plus className="h-3.5 w-3.5" />
        {title}
      </button>
    </div>
  );
}
