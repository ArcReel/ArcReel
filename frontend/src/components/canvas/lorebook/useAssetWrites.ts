import { createContext, useCallback, useContext, useState } from "react";

type TrackWrite = <T>(work: Promise<T>) => Promise<T>;

const AssetWriteContext = createContext<TrackWrite | null>(null);

export const AssetWriteProvider = AssetWriteContext.Provider;

export function useAssetWrites(): { writing: boolean; track: TrackWrite } {
  const [count, setCount] = useState(0);
  const track = useCallback(async <T,>(work: Promise<T>) => {
    setCount((c) => c + 1);
    try {
      return await work;
    } finally {
      setCount((c) => c - 1);
    }
  }, []);
  return { writing: count > 0, track };
}

export function useTrackWrite(): TrackWrite {
  const track = useContext(AssetWriteContext);
  return useCallback(<T,>(work: Promise<T>) => (track ? track(work) : work), [track]);
}
