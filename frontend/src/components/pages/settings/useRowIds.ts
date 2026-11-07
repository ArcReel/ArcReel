import { useState } from "react";

/*
 * Client row IDs for editable/addable/removable lists: React keys only, outside data and saved requests.
 * Rows lack data identities (price tiers, path lists), or identities are edited fields (header/variable names).
 * Index/field keys let subsequent rows inherit others' input states after middle deletion; renaming rebuilds inputs and loses focus.
 */

interface IndexedIds {
  ids: string[];
  next: number;
}

function freshIds(count: number, start: number): IndexedIds {
  return { ids: Array.from({ length: count }, (_, i) => `row-${start + i}`), next: start + count };
}

/**
 * Array-row IDs correspond one-to-one. Call `append`/`remove` in the same handlers issuing `onChange`;
 * mismatched row/ID counts mean external whole-value replacement (official adoption/discard), rebuilding all IDs.
 */
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

/**
 * Named-record row IDs: call `rename` within the same handler, moving IDs with names.
 * Object enumeration moves integer-like keys first, potentially changing row positions during renames; never match by index.
 * Unseen names (new rows/external replacements) receive IDs during rendering.
 */
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
