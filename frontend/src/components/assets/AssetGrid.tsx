import { AssetCard } from "./AssetCard";
import type { Asset } from "@/types/asset";

interface Props {
  assets: Asset[];
  onEdit: (a: Asset) => void;
  onDelete: (a: Asset) => void;
}

export function AssetGrid({ assets, onEdit, onDelete }: Props) {
  return (
    <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 gap-3">
      {assets.map((a) => (
        <AssetCard key={a.id} asset={a} onEdit={onEdit} onDelete={onDelete} />
      ))}
    </div>
  );
}
