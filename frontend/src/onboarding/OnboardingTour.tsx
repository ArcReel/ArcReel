
import { useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useSearch } from "wouter";
import { useTranslation } from "react-i18next";
import { useAuthStore } from "@/stores/auth-store";
import { useOnboardingStore } from "@/stores/onboarding-store";
import { APP_PROJECT_WORKSPACE_PATTERN, APP_TOP_LEVEL_ROUTES } from "@/app-routes";
import { buildTourSteps } from "./steps";
import { startTour, type TourLabels } from "./tour";

export function OnboardingTour() {
  const { t } = useTranslation("onboarding");
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const [location, navigate] = useLocation();
  const search = useSearch();
  const seen = useOnboardingStore((s) => s.seen);
  const active = useOnboardingStore((s) => s.active);

  const normalizedLocation = location.toLowerCase().replace(/(.)\/$/, "$1");
  const inMainUi =
    isAuthenticated &&
    (normalizedLocation === "/" ||
      (APP_TOP_LEVEL_ROUTES as readonly string[]).includes(normalizedLocation) ||
      APP_PROJECT_WORKSPACE_PATTERN.test(normalizedLocation));

  const steps = useMemo(() => buildTourSteps(t), [t]);
  const tourRoutes = useMemo(
    () => new Set(steps.map((s) => s.route).filter((route): route is string => Boolean(route))),
    [steps],
  );

  const stepIndexRef = useRef(0);
  const [stepIndex, setStepIndex] = useState(0);
  const setStep = (index: number) => {
    stepIndexRef.current = index;
    setStepIndex(index);
  };

  const onTourRoute = tourRoutes.has(normalizedLocation);
  const requiredRoute = steps[stepIndex]?.route ?? null;
  const requiredQuery = steps[stepIndex]?.query;
  const querySatisfied =
    !requiredQuery ||
    Object.entries(requiredQuery).every(([key, value]) => new URLSearchParams(search).get(key) === value);

  const currentStep = steps[stepIndex];
  const interactiveTarget = currentStep?.interactive ? currentStep.interactiveTarget : undefined;
  const enteredInteractiveTarget =
    interactiveTarget !== undefined &&
    (normalizedLocation === interactiveTarget || normalizedLocation.startsWith(`${interactiveTarget}/`));
  useEffect(() => {
    if (
      !active ||
      !inMainUi ||
      !requiredRoute ||
      (normalizedLocation === requiredRoute && querySatisfied) ||
      enteredInteractiveTarget
    )
      return;
    navigate(
      requiredQuery ? `${requiredRoute}?${new URLSearchParams(requiredQuery).toString()}` : requiredRoute,
      { replace: true },
    );
  }, [
    active,
    inMainUi,
    requiredRoute,
    requiredQuery,
    querySatisfied,
    normalizedLocation,
    navigate,
    enteredInteractiveTarget,
  ]);

  useEffect(() => {
    if (!inMainUi) return;
    const controller = new AbortController();
    void useOnboardingStore.getState().loadStatus({ signal: controller.signal });
    return () => controller.abort();
  }, [inMainUi]);

  useEffect(() => {
    if (!inMainUi || seen !== false) return;
    useOnboardingStore.getState().start();
  }, [inMainUi, seen]);

  useEffect(() => {
    if (!active || !enteredInteractiveTarget) return;
    const next = stepIndexRef.current + 1;
    if (next >= steps.length) return;
    setStep(next);
  }, [active, enteredInteractiveTarget, steps]);

  useEffect(() => {
    if (!active || !onTourRoute || enteredInteractiveTarget) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- 引导退出时把步号复位到起点，是有意的受控重置，下次开启从头开始
      if (!active) setStep(0);
      return;
    }
    const labels: TourLabels = {
      next: t("next"),
      prev: t("prev"),
      done: t("done"),
      skip: t("skip"),
      close: t("close"),
      progress: (current, total) => t("progress", { current, total }),
    };
    const handle = startTour(steps, labels, {
      onExit: () => useOnboardingStore.getState().exit(),
      startIndex: stepIndexRef.current,
      onStepChange: setStep,
    });
    return () => {
      setStep(handle.currentIndex());
      handle.dispose();
    };
  }, [active, onTourRoute, enteredInteractiveTarget, steps, t]);

  return null;
}
