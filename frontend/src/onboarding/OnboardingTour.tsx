/**
 * Onboarding mounts at route roots, surviving navigation without rendering DOM itself; driver.js attaches
 * overlays/popovers to body.
 *
 * Four responsibilities:
 * 1. Query seen state once after main-screen admission: authenticated after login, anonymous after auth status
 *    permits, both represented by `isAuthenticated`. Login pages remain excluded.
 * 2. Automatically start unseen onboarding once.
 * 3. When active and current pathname/declared queries differ from required steps (replay from settings,
 *    cross-page transitions or section changes), navigate first,
 *    positioning anchors/content correctly.
 * 4. Drive driver.js while active stores/current tour-covered routes permit; automatic starts and settings
 *    replay share paths, indistinguishable to components.
 *
 * Outlines span lobby/settings/demo workbenches (overview/characters/episode boards). driver.js
 * lives on `document.body` outside React; stable route predicates across those pages prevent effect-4
 * teardown/rebuild. Cross-page Next uses `tour.ts`
 * `onStepChange` to synchronously report upcoming indices before highlights, allowing advance navigation
 * without restarting drivers.
 */

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

  // Activate only known main routes; unmatched 404/login stay excluded, or bad links/old bookmarks
  // autostart onboarding and closing incorrectly persists global seen states.
  // /app/settings and /app/assets have no subroutes; prefix matches misclassify /app/settings/unknown
  // as main screens. Unregistered project subpaths show canvas empty states, also excluded;
  // match APP_PROJECT_WORKSPACE_PATTERN exactly.
  // wouter regexparam is case-insensitive, with optional trailing slash outside loose mode (patterns end
  // `\/?$`); lowercase/strip trailing slashes before comparison, preventing valid variants
  // rendered by wouter from falsely failing main-screen detection.
  const normalizedLocation = location.toLowerCase().replace(/(.)\/$/, "$1");
  const inMainUi =
    isAuthenticated &&
    (normalizedLocation === "/" ||
      (APP_TOP_LEVEL_ROUTES as readonly string[]).includes(normalizedLocation) ||
      APP_PROJECT_WORKSPACE_PATTERN.test(normalizedLocation));

  // Rebuild outlines only for languages, sharing render/effect copies to avoid retranslating every render.
  const steps = useMemo(() => buildTourSteps(t), [t]);
  // Derive covered routes from outlines, never duplicate constants; new-page steps only declare
  // `route`, automatically updating coverage without synchronization gaps.
  const tourRoutes = useMemo(
    () => new Set(steps.map((s) => s.route).filter((route): route is string => Boolean(route))),
    [steps],
  );

  // Zero-based step indices: refs supply initial driver-effect values without dependencies (steps must not
  // restart drivers), state drives reactive route effects. Always synchronize writes.
  const stepIndexRef = useRef(0);
  const [stepIndex, setStepIndex] = useState(0);
  const setStep = (index: number) => {
    stepIndexRef.current = index;
    setStepIndex(index);
  };

  // Driver effect (4) tests route membership, lobby/settings, rather than step-by-step equality,
  // avoiding driver reconstruction during cross-page steps.
  const onTourRoute = tourRoutes.has(normalizedLocation);
  const requiredRoute = steps[stepIndex]?.route ?? null;
  const requiredQuery = steps[stepIndex]?.query;
  // Check declared queries too: equal pathnames do not imply correct section content, such as settings
  // `section`. Switching settings steps relies on this predicate to navigate Providers→Agent
  // and back.
  const querySatisfied =
    !requiredQuery ||
    Object.entries(requiredQuery).every(([key, value]) => new URLSearchParams(search).get(key) === value);

  // 0. Active onboarding with mismatched pathname/declared queries navigates first,
  //    allowing anchors/content to settle. Cross-page steps (tour.ts onStepChange), same-page
  //    section changes and replay from noninitial routes all use this.
  //
  //    `interactive` steps differ: actions navigate away from requiredRoute into demos,
  //    unlike driver-effect (3) cross-page transitions; never pull users back then.
  //
  //    Exempt only declared `interactiveTarget` and subroutes, never all destinations outside
  //    requiredRoute. Other destinations always return, inside or outside tour coverage: other
  //    tour routes such as Settings, and unrelated main routes such as libraries both
  //    return, or drivers retain demo-card steps without anchors and center mismatched popovers.
  //    `interactive` and ordinary steps therefore handle unrelated navigation identically, except
  //    one declared extra target. Never predicate on destinations outside tour coverage:
  //    demo workbench routes now belong to `tourRoutes`, which would send new arrivals back to lobbies.
  //
  //    Following entry actions advances indices through effect (2.5), unifying card clicks/Next
  //    and immediately continuing workbench explanations instead of suspension.
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
    // Replace history: onboarding corrective navigation is not user-chosen destinations. Repeated settings-step
    // navigation would otherwise fill history with intermediate states, requiring many Back presses
    // to leave. User-driven demo-card navigation bypasses here, retaining normal push.
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

  // 1. Query seen states.
  useEffect(() => {
    if (!inMainUi) return;
    const controller = new AbortController();
    void useOnboardingStore.getState().loadStatus({ signal: controller.signal });
    return () => controller.abort();
  }, [inMainUi]);

  // 2. Unseen → automatically start once; exit sets seen, preventing repetition.
  useEffect(() => {
    if (!inMainUi || seen !== false) return;
    useOnboardingStore.getState().start();
  }, [inMainUi, seen]);

  // 2.5 Following `interactive` entries into demos advances steps,
  //     matching Next. During that frame effect (3) first disposes old drivers using enteredInteractiveTarget;
  //     advancing here makes enteredInteractiveTarget false for noninteractive new steps,
  //     letting effect (3) rebuild on new indices and continue workbench explanations.
  useEffect(() => {
    if (!active || !enteredInteractiveTarget) return;
    const next = stepIndexRef.current + 1;
    if (next >= steps.length) return;
    setStep(next);
  }, [active, enteredInteractiveTarget, steps]);

  // 3. Drive driver.js.
  //
  // Copy enters drivers at construction; UI-language changes (new `t` identity) require rebuilding.
  // Use `dispose()` without exit records, preserving current indices through rebuilds.
  useEffect(() => {
    // Leaving tour-covered routes (for example browser Back to login) disposes active drivers without
    // recording exits/seen, retaining indices for continuation upon return. Following
    // `interactive` entries also disposes old instances that frame; effect (2.5) then advances
    // indices and this effect reconstructs, visually continuing naturally.
    if (!active || !onTourRoute || enteredInteractiveTarget) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- intentional controlled reset to initial steps on exit, starting future tours from beginning
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
    // Indices intentionally excluded from dependencies (stepIndexRef is exempt from ref-read lint): step changes
    // must not restart effects, or every Next tears down/rebuilds drivers.
  }, [active, onTourRoute, enteredInteractiveTarget, steps, t]);

  return null;
}
