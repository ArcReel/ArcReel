import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { Loader2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Router, useLocation, useSearch, type AroundNavHandler } from "wouter";

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogBody,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";

export interface LeaveGuardOptions {
  /** 有未保存修改；为 false 时不拦截。 */
  dirty: boolean;
  /**
   * 保存请求在途。此时的离开先等保存落定再判断：成功且没有新修改就直接放行，失败或又有修改才询问。
   * 否则「放弃修改」会放走已经发出的保存，「保存并离开」会重复提交。
   */
  saving?: boolean;
  /** 「保存并离开」时调用，返回是否保存成功。失败时留在原处，错误由编辑单元自己显示。 */
  save: () => Promise<boolean>;
  /** 「放弃修改」时调用。不传时只放行跳转，适用于被拦截的出口都会卸载或重新加载编辑单元的页面。 */
  discard?: () => void;
  /** 对话框标题，如「分镜 3 有未保存的修改」；只有一个编辑单元有修改时采用。 */
  title?: string;
  /** 对返回 true 的应用内导航不拦截。参数是带查询串的目标路径。 */
  allowNavigation?: (to: string) => boolean;
}

/**
 * 被离开拦截包住的动作。同步动作返回 `void`，放行即丢弃修改。会落定的动作（删除、移除这类请求）
 * 返回 `Promise<boolean>`：成功时才丢弃各编辑单元的修改，失败、被服务端要求再次确认或被占用拦下时
 * 返回 `false`，修改原样保留。动作自己负责提示失败原因；reject 按失败处理，错误照常抛出。
 */
export type LeaveAction = () => void | Promise<boolean>;

export interface ConfirmLeaveOptions {
  /** 第三个按钮的文案，缺省为「保存并离开」；如切换分镜时传「保存并切换」。 */
  saveLabel?: string;
}

interface LeaveRequest extends ConfirmLeaveOptions {
  /** 被这次离开影响的编辑单元。 */
  unitIds: string[];
  title?: string;
  proceed: LeaveAction;
}

interface RequestLeaveOptions extends ConfirmLeaveOptions {
  to?: string;
  /** 来自被截住的浏览器前进后退：异步动作在途期间，已放弃的修改也照常询问。 */
  fromHistory?: boolean;
}

interface PendingLeave {
  proceed: LeaveAction;
  options: RequestLeaveOptions;
}

interface RegisteredUnit extends LeaveGuardOptions {
  /** 用户已对一个仍在途的异步动作选择「放弃修改」：动作落定前为 true。 */
  setDiscarding: (discarding: boolean) => void;
}

interface LeaveGuardRegistry {
  register: (id: string, unit: RegisteredUnit) => () => void;
  confirmLeave: (proceed: LeaveAction, options?: ConfirmLeaveOptions) => void;
  hasUnsavedChanges: () => boolean;
}

function isPromise(value: unknown): value is Promise<boolean> {
  return typeof (value as Promise<boolean> | undefined)?.then === "function";
}

const LeaveGuardContext = createContext<LeaveGuardRegistry | null>(null);

/**
 * 把一个编辑单元登记到离开拦截：挂载期间有未保存修改时，应用内路由跳转、`useConfirmLeave`
 * 包住的切换与关闭标签页都会先询问。`useEditUnit` 已自动登记；自行管理表单状态的页面直接调用。
 * 函数型参数需传稳定引用。
 *
 * 返回 true 表示用户已选择放弃修改、正等被包住的异步动作落定：修改仍在，成功后才丢弃。
 * 参与外部移除保留的单元此时不再要求保留，动作本身带来的移除直接生效。
 */
export function useLeaveGuard({ dirty, saving, save, discard, title, allowNavigation }: LeaveGuardOptions): boolean {
  const registry = useContext(LeaveGuardContext);
  const id = useId();
  const [discarding, setDiscarding] = useState(false);
  useEffect(() => {
    if (!registry) return;
    return registry.register(id, { dirty, saving, save, discard, title, allowNavigation, setDiscarding });
  }, [registry, id, dirty, saving, save, discard, title, allowNavigation]);
  return discarding;
}

/**
 * 主从布局内切换选中项（供应商、记忆文件、分镜、资产的上一个与下一个）时使用：
 * 有未保存修改先弹出拦截对话框，用户放行后才执行 `proceed`。切换类的 `proceed` 需同步完成切换，
 * 其中发起的路由跳转不再重复拦截。选中项记在 URL 里、经路由跳转切换的，路由拦截已经覆盖，不必再包。
 *
 * 删除、移除这类可能失败的动作返回 `Promise<boolean>`（见 `LeaveAction`）：用户选择放弃修改后，
 * 修改保留到动作落定，成功才丢弃；动作在途期间，这些已放弃的修改不再拦截它自己发起的路由跳转与
 * `useConfirmLeave`，其他编辑单元的新修改照常询问。
 */
export function useConfirmLeave(): (proceed: LeaveAction, options?: ConfirmLeaveOptions) => void {
  const registry = useContext(LeaveGuardContext);
  return useCallback(
    (proceed: LeaveAction, options?: ConfirmLeaveOptions) => {
      if (registry) registry.confirmLeave(proceed, options);
      else void proceed();
    },
    [registry],
  );
}

/**
 * 返回一个稳定的查询函数：此刻是否有挂载中的编辑单元带着未保存修改。
 * 供不该打断编辑的自动行为使用（如 Agent 改动带来的自动定位），用户发起的切换仍走 `useConfirmLeave`。
 */
export function useHasUnsavedChanges(): () => boolean {
  const registry = useContext(LeaveGuardContext);
  return useCallback(() => registry?.hasUnsavedChanges() ?? false, [registry]);
}

function currentUrl(): string {
  return window.location.pathname + window.location.search + window.location.hash;
}

/** 目标与当前地址的路径和查询参数都相同（参数顺序不计）。`to` 可以是只有查询串的相对地址。 */
function isSameLocation(to: string, here: string): boolean {
  const current = new URL(here, "http://leave-guard.invalid");
  const target = new URL(to, current);
  current.searchParams.sort();
  target.searchParams.sort();
  return target.pathname === current.pathname && target.search === current.search;
}

/**
 * 离开拦截的注册中心与唯一的拦截对话框，挂在路由根部（`base` 为空的位置）。
 * 应用内路由跳转经 wouter 的 `aroundNav` 拦截；浏览器前进后退先退回原地址再询问；
 * 关闭标签页与刷新用浏览器原生的 `beforeunload` 提示。
 */
export function LeaveGuardProvider({ children }: { children: ReactNode }) {
  const { t } = useTranslation("common");
  const [path] = useLocation();
  const search = useSearch();
  // 路由的当前地址（路径加查询串），供 aroundNav 识别跳到当前地址
  const hereRef = useRef("");
  useLayoutEffect(() => {
    hereRef.current = search ? `${path}?${search}` : path;
  }, [path, search]);
  const unitsRef = useRef(new Map<string, RegisteredUnit>());
  // 用户已放行的那次切换在同步执行期间发起的跳转，不再重复拦截
  const passingRef = useRef(false);
  // 已放行、仍在途的异步动作涉及的编辑单元及其动作数：落定前这些单元的修改已获准丢弃，
  // 不再拦截动作自己发起的跳转；其他单元照常询问。浏览器前进后退照常询问
  const settlingRef = useRef(new Map<string, number>());
  // 用户放行被截住的前进后退后，由 history.back() 引起的那次 popstate 直接交给 wouter
  const releasingPopRef = useRef(false);
  // 等在途保存落定的离开请求；登记变化时重新判断，只保留最近一次
  const pendingLeaveRef = useRef<PendingLeave | null>(null);
  const lastUrlRef = useRef("");
  const [request, setRequest] = useState<LeaveRequest | null>(null);
  const [open, setOpen] = useState(false);
  const [saving, setSaving] = useState(false);

  /** 这次离开会影响的编辑单元：排除放行该目标的单元。 */
  const leavingUnits = useCallback(
    (to?: string) => [...unitsRef.current].filter(([, unit]) => !(to !== undefined && unit.allowNavigation?.(to))),
    [],
  );

  const dirtyUnitIds = useCallback(
    (to?: string) => leavingUnits(to).flatMap(([id, unit]) => (unit.dirty ? [id] : [])),
    [leavingUnits],
  );

  /**
   * 执行用户放行的动作，并丢弃 `discardIds` 中各单元的修改。同步动作执行完即丢弃；
   * 异步动作落定且成功后才丢弃，在途期间通知这些单元不再要求外部移除保留，失败时恢复。
   */
  const pass = useCallback((proceed: LeaveAction, discardIds: readonly string[] = []) => {
    const units = () => discardIds.flatMap((id) => unitsRef.current.get(id) ?? []);
    const affected = units();
    let result: void | Promise<boolean>;
    passingRef.current = true;
    try {
      result = proceed();
    } catch (err) {
      for (const unit of affected) unit.discard?.();
      throw err;
    } finally {
      passingRef.current = false;
    }
    if (!isPromise(result)) {
      for (const unit of affected) unit.discard?.();
      return;
    }
    const settling = settlingRef.current;
    for (const id of discardIds) settling.set(id, (settling.get(id) ?? 0) + 1);
    for (const unit of affected) unit.setDiscarding(true);
    const settle = (succeeded: boolean) => {
      for (const id of discardIds) {
        const count = (settling.get(id) ?? 1) - 1;
        if (count > 0) settling.set(id, count);
        else settling.delete(id);
      }
      // 取最新登记：动作成功后已卸载的单元不必再丢弃
      for (const unit of units()) {
        unit.setDiscarding(false);
        if (succeeded) unit.discard?.();
      }
    };
    void result.then(
      (succeeded) => settle(succeeded),
      (err: unknown) => {
        settle(false);
        throw err;
      },
    );
  }, []);

  const requestLeave = useCallback(
    (proceed: LeaveAction, options: RequestLeaveOptions = {}) => {
      if (passingRef.current) {
        void proceed();
        return;
      }
      const units = leavingUnits(options.to).filter(([id]) => options.fromHistory || !settlingRef.current.has(id));
      if (units.some(([, unit]) => unit.saving)) {
        pendingLeaveRef.current = { proceed, options };
        return;
      }
      const unitIds = units.flatMap(([id, unit]) => (unit.dirty ? [id] : []));
      if (unitIds.length === 0) {
        void proceed();
        return;
      }
      const title = unitIds.length === 1 ? unitsRef.current.get(unitIds[0])?.title : undefined;
      setRequest({ unitIds, title, proceed, saveLabel: options.saveLabel });
      setOpen(true);
    },
    [leavingUnits],
  );

  const registry = useMemo<LeaveGuardRegistry>(() => {
    // 编辑单元的保存落定后会更新登记，此时重新判断被推迟的离开；放到 effect 之外执行
    const retryPendingLeave = () => {
      const pending = pendingLeaveRef.current;
      if (!pending) return;
      pendingLeaveRef.current = null;
      queueMicrotask(() => requestLeave(pending.proceed, pending.options));
    };
    return {
      register: (id, unit) => {
        unitsRef.current.set(id, unit);
        retryPendingLeave();
        return () => {
          unitsRef.current.delete(id);
          retryPendingLeave();
        };
      },
      confirmLeave: (proceed, options) => requestLeave(proceed, options),
      hasUnsavedChanges: () => dirtyUnitIds().length > 0,
    };
  }, [requestLeave, dirtyUnitIds]);

  const aroundNav = useCallback<AroundNavHandler>(
    (go, to, options) => {
      const navigate = () => {
        go(to, options);
        lastUrlRef.current = currentUrl();
      };
      // 跳到当前地址（如再点一次已选中的分区）不会卸载任何编辑单元，不询问
      if (isSameLocation(to, hereRef.current)) navigate();
      else requestLeave(navigate, { to });
    },
    [requestLeave],
  );

  // 先于 wouter 的位置订阅（子组件的 passive effect）登记，并在捕获阶段监听，
  // 保证有未保存修改时 wouter 收不到这次 popstate。
  useLayoutEffect(() => {
    lastUrlRef.current = currentUrl();
    const onPopState = (event: PopStateEvent) => {
      const target = currentUrl();
      // 保存在途的单元即使此刻没有修改也不放行：保存失败或期间又改过时还要询问
      const unaffected =
        dirtyUnitIds(target).length === 0 && !leavingUnits(target).some(([, unit]) => unit.saving);
      if (releasingPopRef.current || passingRef.current || unaffected) {
        releasingPopRef.current = false;
        lastUrlRef.current = target;
        return;
      }
      // 浏览器前进后退不经过 aroundNav，地址已经变了：先压入离开前的地址，目标记录正好在它前一条。
      // 用户放行后退回一条，回到浏览器原本要去的那条历史记录，不另压新记录
      event.stopImmediatePropagation();
      window.history.pushState(null, "", lastUrlRef.current);
      requestLeave(
        () => {
          releasingPopRef.current = true;
          window.history.back();
        },
        { to: target, fromHistory: true },
      );
    };
    window.addEventListener("popstate", onPopState, { capture: true });
    return () => window.removeEventListener("popstate", onPopState, { capture: true });
  }, [dirtyUnitIds, leavingUnits, requestLeave]);

  useEffect(() => {
    const onBeforeUnload = (event: BeforeUnloadEvent) => {
      if (dirtyUnitIds().length > 0) event.preventDefault();
    };
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => window.removeEventListener("beforeunload", onBeforeUnload);
  }, [dirtyUnitIds]);

  const discardAll = () => {
    if (!request) return;
    setOpen(false);
    pass(request.proceed, request.unitIds);
  };

  const saveAll = async () => {
    if (!request) return;
    setSaving(true);
    let saved = true;
    for (const id of request.unitIds) {
      // 每次取最新登记：前一个单元保存后会重新渲染并更新登记
      const unit = unitsRef.current.get(id);
      if (unit && !(await unit.save())) {
        saved = false;
        break;
      }
    }
    setSaving(false);
    setOpen(false);
    // 保存失败留在原处，错误显示在编辑单元的保存栏或提示条上
    if (saved) pass(request.proceed);
  };

  return (
    <LeaveGuardContext.Provider value={registry}>
      <Router aroundNav={aroundNav}>{children}</Router>
      <AlertDialog
        open={open}
        onOpenChange={(next) => {
          // 保存中不响应 Esc，避免请求在途时对话框先关掉
          if (!next && !saving) setOpen(false);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{request?.title ?? t("unsaved_changes")}</AlertDialogTitle>
          </AlertDialogHeader>
          <AlertDialogBody tabIndex={0} role="region" aria-label={request?.title ?? t("unsaved_changes")}>
            <AlertDialogDescription>{t("leave_dialog_description")}</AlertDialogDescription>
          </AlertDialogBody>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={saving}>{t("keep_editing")}</AlertDialogCancel>
            <AlertDialogAction variant="destructive" disabled={saving} onClick={discardAll}>
              {t("discard_changes")}
            </AlertDialogAction>
            <AlertDialogAction disabled={saving} onClick={() => void saveAll()}>
              {saving ? <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" /> : null}
              {request?.saveLabel ?? t("save_and_leave")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </LeaveGuardContext.Provider>
  );
}
