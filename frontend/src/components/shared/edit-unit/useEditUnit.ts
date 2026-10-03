import { useCallback, useEffect, useState } from "react";

import { errMsg } from "@/utils/async";

import { useLeaveGuard } from "./LeaveGuard";

/** 保存状态：「已保存」短暂显示后回到 idle。 */
export type SaveStatus = "idle" | "saving" | "saved" | "error";

/** 「已保存」在保存栏或提示条上停留的时长。 */
const SAVED_DISPLAY_MS = 2000;

export interface EditUnitOptions<T> {
  /**
   * 已保存的内容：加载结果，以及之后 SSE 推送、Agent 改写带来的新值。
   * 没有未保存修改时新值直接生效；有未保存修改时保留修改，并标出外部更新。
   */
  source: T;
  /**
   * 提交未保存修改。`savedValue` 是提交前的已保存内容，供按字段生成增量 PATCH。
   * 返回服务端保存后的内容；不返回时以提交的内容为准。抛错即保存失败，错误信息显示在保存栏或提示条上。
   * 需传稳定引用（`useCallback`）。
   */
  save: (value: T, savedValue: T) => Promise<T | void>;
  /** 判断两份内容是否相同，默认按 JSON 序列化比较。需传稳定引用（或模块级函数）。 */
  isEqual?: (a: T, b: T) => boolean;
  /** 离开拦截对话框的标题，如「分镜 3 有未保存的修改」；缺省为通用标题。 */
  leaveTitle?: string;
  /**
   * 对返回 true 的应用内导航不拦截，用于不会卸载本编辑单元的跳转（如同一编辑单元的分页切换）。
   * 参数是带查询串的目标路径。需传稳定引用。
   */
  allowNavigation?: (to: string) => boolean;
}

export interface SaveAndGenerateOptions {
  /** 保存前的确认（如「重新生成会让下游失效」），返回 false 时什么都不保存、不生成。 */
  confirm?: () => boolean | Promise<boolean>;
}

export interface EditUnit<T> {
  /** 当前内容，包含未保存修改。 */
  value: T;
  /** 已保存的内容。按字段子集判断修改（如侧栏分页的修改标记）时与 `value` 比较。 */
  savedValue: T;
  setValue: (next: T | ((prev: T) => T)) => void;
  /** 有未保存修改。 */
  dirty: boolean;
  status: SaveStatus;
  /** 最近一次保存失败的原因；再次编辑、放弃或保存成功时清除。 */
  error: string | null;
  /** 有未保存修改期间，已保存内容被外部更新。放弃修改即采用新内容。 */
  externallyUpdated: boolean;
  /** 提交未保存修改，返回是否成功；没有修改时直接返回 true。 */
  save: () => Promise<boolean>;
  /** 丢弃未保存修改，回到最新的已保存内容。 */
  discard: () => void;
  /**
   * 「保存并生成」：确认（如需要）→ 保存（有修改时）→ 生成。
   * 确认被取消时什么都不保存，保存失败时不生成；返回是否执行了生成。
   */
  saveAndGenerate: (generate: () => unknown, options?: SaveAndGenerateOptions) => Promise<boolean>;
}

interface UnitState<T> {
  value: T;
  saved: T;
  /** 最近一次见到的 `source`，用来识别外部更新。 */
  seenSource: T;
  status: SaveStatus;
  error: string | null;
  externallyUpdated: boolean;
}

function jsonEqual<T>(a: T, b: T): boolean {
  return a === b || JSON.stringify(a) === JSON.stringify(b);
}

/**
 * 编辑单元：一次显式保存所提交的界面范围（一个设置视图、一张资产、一个分镜详情、一个记忆文件等）。
 * 单元内的字段先进入未保存修改，由 `save` 统一提交；挂载期间自动登记到离开拦截。
 */
export function useEditUnit<T>({
  source,
  save: submit,
  isEqual = jsonEqual,
  leaveTitle,
  allowNavigation,
}: EditUnitOptions<T>): EditUnit<T> {
  const [state, setState] = useState<UnitState<T>>(() => ({
    value: source,
    saved: source,
    seenSource: source,
    status: "idle",
    error: null,
    externallyUpdated: false,
  }));

  // 已保存内容来自外部的变化：在渲染期同步（React 的「随 props 调整 state」写法），
  // 避免先用旧内容渲染一帧。与当前已保存内容相同的推送（如自己保存后的回显）不算外部更新。
  let current = state;
  if (!isEqual(source, state.seenSource)) {
    if (isEqual(source, state.saved)) {
      current = { ...state, seenSource: source };
    } else if (isEqual(state.value, state.saved)) {
      current = { ...state, seenSource: source, saved: source, value: source, externallyUpdated: false };
    } else {
      current = {
        ...state,
        seenSource: source,
        saved: source,
        externallyUpdated: !isEqual(state.value, source),
      };
    }
    setState(current);
  }

  const { value, saved, status, error } = current;
  const dirty = !isEqual(value, saved);

  useEffect(() => {
    if (status !== "saved") return;
    const timer = setTimeout(() => {
      setState((prev) => (prev.status === "saved" ? { ...prev, status: "idle" } : prev));
    }, SAVED_DISPLAY_MS);
    return () => clearTimeout(timer);
  }, [status]);

  const setValue = useCallback((next: T | ((prev: T) => T)) => {
    setState((prev) => ({
      ...prev,
      value: typeof next === "function" ? (next as (prev: T) => T)(prev.value) : next,
      status: prev.status === "saving" ? prev.status : "idle",
      error: null,
    }));
  }, []);

  const discard = useCallback(() => {
    setState((prev) => ({ ...prev, value: prev.saved, status: "idle", error: null, externallyUpdated: false }));
  }, []);

  const save = useCallback(async () => {
    if (!dirty) return true;
    const submitted = value;
    setState((prev) => ({ ...prev, status: "saving", error: null }));
    try {
      const result = await submit(submitted, saved);
      const next = result === undefined ? submitted : (result as T);
      setState((prev) => ({
        ...prev,
        saved: next,
        // 保存期间没有继续编辑时，采用服务端规范化后的内容；继续编辑过的保留新修改
        value: isEqual(prev.value, submitted) ? next : prev.value,
        status: "saved",
        error: null,
        // 坚持保存时后写者生效，外部更新的标记随之清除
        externallyUpdated: false,
      }));
      return true;
    } catch (err) {
      setState((prev) => ({ ...prev, status: "error", error: errMsg(err) }));
      return false;
    }
  }, [dirty, value, saved, submit, isEqual]);

  const saveAndGenerate = useCallback(
    async (generate: () => unknown, options?: SaveAndGenerateOptions) => {
      if (options?.confirm && !(await options.confirm())) return false;
      if (!(await save())) return false;
      await generate();
      return true;
    },
    [save],
  );

  useLeaveGuard({ dirty, saving: status === "saving", save, discard, title: leaveTitle, allowNavigation });

  return {
    value,
    savedValue: saved,
    setValue,
    dirty,
    status,
    error,
    externallyUpdated: current.externallyUpdated && dirty,
    save,
    discard,
    saveAndGenerate,
  };
}
