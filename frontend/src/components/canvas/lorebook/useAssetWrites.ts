import { createContext, useCallback, useContext, useState } from "react";

type TrackWrite = <T>(work: Promise<T>) => Promise<T>;

const AssetWriteContext = createContext<TrackWrite | null>(null);

/** Write registration supplied by the detail editor: uploads, deletions and alias additions/removals in each section use it to register occupancy. */
export const AssetWriteProvider = AssetWriteContext.Provider;

/**
 * Detail-editor write occupancy: `writing` is true while any section has a write request in flight, disabling rename, generation and other writes together.
 * These requests use local state outside the task queue and are invisible to `rejectIfAssetBusy`.
 */
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

/** Register one section write request; return it unchanged outside the detail editor. */
export function useTrackWrite(): TrackWrite {
  const track = useContext(AssetWriteContext);
  return useCallback(<T,>(work: Promise<T>) => (track ? track(work) : work), [track]);
}
