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
  dirty: boolean;
  saving?: boolean;
  save: () => Promise<boolean>;
  discard?: () => void;
  title?: string;
  allowNavigation?: (to: string) => boolean;
}

export type LeaveAction = () => void | Promise<boolean>;

export interface ConfirmLeaveOptions {
  saveLabel?: string;
}

interface LeaveRequest extends ConfirmLeaveOptions {
  unitIds: string[];
  title?: string;
  proceed: LeaveAction;
}

interface RequestLeaveOptions extends ConfirmLeaveOptions {
  to?: string;
  fromHistory?: boolean;
}

interface PendingLeave {
  proceed: LeaveAction;
  options: RequestLeaveOptions;
}

interface RegisteredUnit extends LeaveGuardOptions {
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

export function useHasUnsavedChanges(): () => boolean {
  const registry = useContext(LeaveGuardContext);
  return useCallback(() => registry?.hasUnsavedChanges() ?? false, [registry]);
}

function currentUrl(): string {
  return window.location.pathname + window.location.search + window.location.hash;
}

function isSameLocation(to: string, here: string): boolean {
  const current = new URL(here, "http://leave-guard.invalid");
  const target = new URL(to, current);
  current.searchParams.sort();
  target.searchParams.sort();
  return target.pathname === current.pathname && target.search === current.search;
}

export function LeaveGuardProvider({ children }: { children: ReactNode }) {
  const { t } = useTranslation("common");
  const [path] = useLocation();
  const search = useSearch();
  const hereRef = useRef("");
  useLayoutEffect(() => {
    hereRef.current = search ? `${path}?${search}` : path;
  }, [path, search]);
  const unitsRef = useRef(new Map<string, RegisteredUnit>());
  const passingRef = useRef(false);
  const settlingRef = useRef(new Map<string, number>());
  const releasingPopRef = useRef(false);
  const pendingLeaveRef = useRef<PendingLeave | null>(null);
  const lastUrlRef = useRef("");
  const [request, setRequest] = useState<LeaveRequest | null>(null);
  const [open, setOpen] = useState(false);
  const [saving, setSaving] = useState(false);

  const leavingUnits = useCallback(
    (to?: string) => [...unitsRef.current].filter(([, unit]) => !(to !== undefined && unit.allowNavigation?.(to))),
    [],
  );

  const dirtyUnitIds = useCallback(
    (to?: string) => leavingUnits(to).flatMap(([id, unit]) => (unit.dirty ? [id] : [])),
    [leavingUnits],
  );

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
    try {
      for (const id of request.unitIds) {
        const unit = unitsRef.current.get(id);
        if (unit && !(await unit.save())) {
          saved = false;
          break;
        }
      }
    } finally {
      setSaving(false);
      setOpen(false);
    }
    if (saved) pass(request.proceed);
  };

  return (
    <LeaveGuardContext.Provider value={registry}>
      <Router aroundNav={aroundNav}>{children}</Router>
      <AlertDialog
        open={open}
        onOpenChange={(next) => {
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
