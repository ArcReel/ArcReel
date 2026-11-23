import { useState } from "react";


interface IndexedIds {
  ids: string[];
  next: number;
}

function freshIds(count: number, start: number): IndexedIds {
  return { ids: Array.from({ length: count }, (_, i) => `row-${start + i}`), next: start + count };
}

export function useRowIds(count: number) {
  const [state, setState] = useState(() => freshIds(count, 0));
  let current = state;
  if (state.ids.length !== count) {
    current = freshIds(count, state.next);
    setState(current);
  }
  return {
    ids: current.ids,
    append: () => setState((s) => ({ ids: [...s.ids, `row-${s.next}`], next: s.next + 1 })),
    remove: (index: number) => setState((s) => ({ ...s, ids: s.ids.filter((_, i) => i !== index) })),
  };
}

interface NamedIds {
  byName: Record<string, string>;
  next: number;
}

function withNames(state: NamedIds, names: readonly string[]): NamedIds {
  const byName = { ...state.byName };
  let next = state.next;
  for (const name of names) {
    if (!(name in byName)) byName[name] = `row-${next++}`;
  }
  return { byName, next };
}

export function useNamedRowIds(names: readonly string[]) {
  const [state, setState] = useState(() => withNames({ byName: {}, next: 0 }, names));
  let current = state;
  if (names.some((name) => !(name in state.byName))) {
    current = withNames(state, names);
    setState(current);
  }
  return {
    idOf: (name: string) => current.byName[name],
    rename: (from: string, to: string) =>
      setState((s) => {
        if (from === to || !(from in s.byName)) return s;
        const byName = { ...s.byName, [to]: s.byName[from] };
        delete byName[from];
        return { ...s, byName };
      }),
  };
}
