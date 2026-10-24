import { useState } from "react";

/*
 * 可增删、可编辑的行列表的客户端行 id：只做 React key，不进数据，不随保存发出。
 * 行在数据里没有身份（价目表的档、路径列表），或身份就是正在编辑的字段（请求头名、变量名），
 * 用下标或字段作 key 时，删掉中间一行会让后面的行错位接手别人的输入状态，改名会重建输入框、丢焦点。
 */

interface IndexedIds {
  ids: string[];
  next: number;
}

function freshIds(count: number, start: number): IndexedIds {
  return { ids: Array.from({ length: count }, (_, i) => `row-${start + i}`), next: start + count };
}

/**
 * 数组行的 id，与行一一对应。增行、删行时在发出 `onChange` 的同一个处理函数里调用 `append`、`remove`；
 * 行数与 id 数对不上即外部整体换了值（采用官方值、放弃修改），全部重建。
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
 * 以名字为键的记录行的 id。改名时在同一个处理函数里调用 `rename`，id 跟着名字走：
 * 对象键的枚举顺序会把整数形式的键提到前面，行的位置在改名中可能变化，所以不按下标对应。
 * 没见过的名字（新增的行、外部换进来的值）在渲染时补发 id。
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
