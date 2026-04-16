import { create } from "zustand";
import { API } from "@/api";
import type { Asset, AssetType } from "@/types/asset";

interface AssetsStore {
  byType: Record<AssetType, Asset[]>;
  loading: boolean;
  loadList: (type: AssetType, q?: string) => Promise<void>;
  addAsset: (asset: Asset) => void;
  updateAsset: (asset: Asset) => void;
  deleteAsset: (id: string, type: AssetType) => Promise<void>;
}

export const useAssetsStore = create<AssetsStore>((set) => ({
  byType: { character: [], scene: [], prop: [] },
  loading: false,
  loadList: async (type, q) => {
    set({ loading: true });
    try {
      const res = await API.listAssets({ type, q });
      set((s) => ({ byType: { ...s.byType, [type]: res.items } }));
    } finally {
      set({ loading: false });
    }
  },
  addAsset: (asset) =>
    set((s) => ({
      byType: { ...s.byType, [asset.type]: [asset, ...s.byType[asset.type]] },
    })),
  updateAsset: (asset) =>
    set((s) => ({
      byType: {
        ...s.byType,
        [asset.type]: s.byType[asset.type].map((a) => (a.id === asset.id ? asset : a)),
      },
    })),
  deleteAsset: async (id, type) => {
    await API.deleteAsset(id);
    set((s) => ({
      byType: { ...s.byType, [type]: s.byType[type].filter((a) => a.id !== id) },
    }));
  },
}));
