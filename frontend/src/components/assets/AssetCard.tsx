import { useTranslation } from "react-i18next";
import { Edit2, Trash2, User as UserIcon, Landmark, Package } from "lucide-react";
import { API } from "@/api";
import type { Asset } from "@/types/asset";

interface Props {
  asset: Asset;
  onEdit: (asset: Asset) => void;
  onDelete: (asset: Asset) => void;
}

const TYPE_ICON = { character: UserIcon, scene: Landmark, prop: Package };

export function AssetCard({ asset, onEdit, onDelete }: Props) {
  const { t } = useTranslation("assets");
  const Icon = TYPE_ICON[asset.type];
  const imageUrl = API.getGlobalAssetUrl(asset.image_path, asset.updated_at);

  return (
    <div className="group bg-gray-900 border border-gray-800 rounded-lg overflow-hidden hover:border-gray-600 transition-colors">
      <div className="aspect-[3/4] bg-gradient-to-br from-gray-800 to-gray-700 flex items-center justify-center">
        {imageUrl ? (
          <img src={imageUrl} alt={asset.name} loading="lazy" decoding="async" className="h-full w-full object-cover" />
        ) : (
          <Icon className="h-10 w-10 text-gray-600" />
        )}
      </div>
      <div className="p-3">
        <div className="flex items-start gap-2">
          <div className="flex-1 min-w-0">
            <div className="font-semibold text-sm text-white truncate">{asset.name}</div>
            {asset.description && (
              <div className="mt-1 text-xs text-gray-400 line-clamp-2">{asset.description}</div>
            )}
          </div>
          <div className="flex flex-col gap-1 opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 transition-opacity">
            <button onClick={() => onEdit(asset)} aria-label={t("edit")}
              className="p-1 text-gray-400 hover:text-white rounded focus-visible:opacity-100">
              <Edit2 className="h-3.5 w-3.5" />
            </button>
            <button onClick={() => onDelete(asset)} aria-label={t("delete")}
              className="p-1 text-gray-400 hover:text-red-400 rounded focus-visible:opacity-100">
              <Trash2 className="h-3.5 w-3.5" />
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
