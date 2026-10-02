// PROTOTYPE — #2973：读资产图的版本列表（只读，供查看器与详情显示「第几版」）。

import { useEffect, useState } from "react";
import { API } from "@/api";
import type { VersionInfo } from "@/api/types";

export interface VersionsState {
  current: number;
  versions: VersionInfo[];
}

const cache = new Map<string, VersionsState>();

export function useVersions(projectName: string, resource: string, name: string, enabled = true) {
  const key = `${projectName}/${resource}/${name}`;
  const [state, setState] = useState<VersionsState | null>(() => cache.get(key) ?? null);

  useEffect(() => {
    if (!enabled) return;
    let alive = true;
    const hit = cache.get(key);
    // eslint-disable-next-line react-hooks/set-state-in-effect -- 原型：切换资产时先显示缓存
    setState(hit ?? null);
    if (hit) return;
    API.getVersions(projectName, resource, name)
      .then((r) => {
        const next = { current: r.current_version, versions: r.versions };
        cache.set(key, next);
        if (alive) setState(next);
      })
      .catch(() => {
        if (alive) setState({ current: 0, versions: [] });
      });
    return () => {
      alive = false;
    };
  }, [key, projectName, resource, name, enabled]);

  return state;
}
