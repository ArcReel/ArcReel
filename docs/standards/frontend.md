---
paths:
  - "frontend/src/**"
---

# Frontend

## Resource occupancy and enqueueing

### Controls disabled by resource occupancy check it on opening and submission, and disable sibling controls together

When adding or changing editing, regeneration, upload, asset-library upload, version restoration, or other occupancy-sensitive controls, check three things:

1. Check current occupancy when opening the dialog or panel.
2. Recheck the latest state on submission with `isResourceBusy(kind, projectName, resourceId)` from `frontend/src/stores/tasks-store.ts`. State may change after opening; checking only then leaves a race window.
3. Bind sibling controls on the same resource card to occupancy too.

Occupancy is not only queued tasks. The card's own in-flight writes (`saving`, uploading, renaming) are component-local state invisible to `isResourceBusy`; include that local state in all three checks.

If occupancy changes while confirmation is open, disable one-click destructive confirmations (delete, merge, rename) immediately. Keep input forms editable and explain the reason at submission recheck.

## Onboarding

### Changes to `data-onboarding` elements require checking the whole onboarding chain

Highlights find elements by `data-onboarding`. Anchor names are in `frontend/src/onboarding/anchors.ts`, step outlines in `steps.ts`, and copy in `frontend/src/i18n/*/onboarding.ts`. Type checking validates names; `anchors.test.tsx` checks only that registered anchors exist in mounted scenarios. Nothing at compile time or in tests enforces these three requirements. Failures degrade at runtime to a centered bubble or direct users to nonexistent labels:

- **The attribute remains, and the element still renders unconditionally.** Moving its mount into a condition (empty state, ready data, active tab) makes the step miss its anchor on common paths. The tour does not stop; after `ANCHOR_WAIT_MS` it falls back to a centered bubble and only logs a console warning. Move anchors to an unconditionally mounted container on the same screen and update the entry description in `anchors.ts`.
- **Step copy still describes the element.** If its function, fields, or button availability changes, update the step in every language.
- **Entry names match visible labels.** Refer to sidebar items, tabs, and buttons by their displayed labels; update all locales when renaming them.

Deleting an anchored element removes the entire chain: its `anchors.ts` entry, `steps.ts` step, locale keys, and assertions in `steps.test.ts` and `anchors.test.tsx`.

## Primitives and shadcn-generated files

### Explain intentional upstream deviations in `components/ui/` with one local comment

When changing a primitive's behavior or style for repository requirements (such as a height-limited Dialog Body or opaque backdrop), add one comment at the change explaining why. Upgrades manually merge upstream with `pnpm exec shadcn add <组件> --diff`; undocumented local changes cannot be distinguished and are easily overwritten. These comments explain upstream deviation and are exempt from the general removal of historical-comment descriptions.

Plain lint fixes need no comment. Exempt confirmed false positives with inline `eslint-disable-next-line <规则> -- 理由`.

### Code-like inputs use `Input`/`Textarea` `mono` rather than handwritten monospace classes

`mono` controls only font; `variant` controls only appearance; both may coexist. `InputGroupInput` and `InputGroupTextarea` forward `mono`. Do not put `font-mono` on an input or ancestor: inheritance reaches inputs without detection by `@shadcn/lint`, defeating contract review.

Use `mono` for:

- JSON, code, and request-body editors: endpoint JSON view, body templates, pasted import definitions, pasted trial responses, raw Agent-memory Markdown.
- Model IDs.
- Tokens: API Keys and other credential inputs.
- Request paths/templates: endpoint request, query, and retrieval templates; header names/values; variable names; extraction paths; status values.

Keep proportional fonts for:

- API URLs, such as provider/endpoint default URLs and proxy URLs. Use `spellCheck={false}` where appropriate rather than changing font.
- Names, versions, prompts, and other prose. Login passwords are not tokens and remain proportional too.

### Candidate-only text selection uses Combobox; free-form values use Base UI Autocomplete

Base UI Combobox accepts only candidates, not arbitrary text. Model-ID fields need free-form entry because gateway lists are often incomplete, so use `@base-ui/react/autocomplete`. The shadcn base-nova registry has no Autocomplete; follow `components/agent/ModelIdField`: inputs use `components/ui/input-group`, popup surfaces use `bg-popover`, `shadow-overlay`, and `z-overlay`.

### List every relaxation of styling or layout guards in the PR description

Non-test source under `frontend/src/` is governed by `@shadcn/lint` style rules, scrolling/responsive guards, and class guards in `frontend/eslint.config.js`. Each relaxation legalizes a class of usage within its scope but appears as a single config line, easily missed in review. List each in the PR for judgment of its reason:

- New component variants and `@shadcn/lint` `allow` or `contracts` entries.
- Files added to `VIEWPORT_BREAKPOINT_ALLOWLIST`. Allow only `true` viewport-dependent cases such as standard/compact shell switching and overlay widths. Inner-shell width depends on sidebars and split panes; use container queries there.
- New or expanded configuration blocks exempting `restrictSyntax(...)`.

Configuration composition (flat-config wholesale replacement of `no-restricted-syntax` options) is explained in the config header and checked by `src/lint-class-guards.test.ts`.

### Repository usage of Sheet, DropdownMenu, Command, and Chart

- **Sheet**: both sides default to `w-md`. Change the right width with `data-[side=right]:w-*`; plain `w-*` loses to the primitive's `data-[side=right]:`-prefixed default.
- **DropdownMenu**: irreversible menu actions such as delete use `DropdownMenuItem` with `variant="destructive"`.
- **Command** (often in Popover for searchable selection): `CommandList` is a listbox; pass a localized `label` instead of its default English name. Include only `CommandEmpty`, `CommandGroup`, and `CommandItem`. Put separators, loading, and loading-error messages outside `CommandList`, or axe reports `aria-required-children`. Fixed unfiltered entries (such as all projects) use `Button` or `Link` outside Command. On Popover opening, use `initialFocus` for `CommandInput`.
- **Chart**: wrap in `role="img"` with its name as `aria-label`, immediately followed by an `sr-only` table of the same data for screen-reader access to values (example: `components/usage/UsageTrendChart.tsx`). Series colors reference `index.css` tokens; tooltip surfaces match overlays (`bg-popover`, `shadow-overlay`).

## Colors and text hierarchy

### Use `index.css` semantic color tokens; express shades through `opacity` modifiers

`no-raw-colors` and `no-inline-styles` in `@shadcn/lint` block raw values and inline colors. Review checks what lint cannot:

- Use base colors plus opacity for pale backgrounds, borders, and selection (`bg-primary/15`, `border-border/50`), without adding shade-specific tokens that create near-duplicate semantic colors. The old palette's variants were removed on this principle. In CSS use `color-mix(in oklab, var(--primary) 15%, transparent)`.
- Form-control borders use `border-input` without opacity. `--input` stays at least 3:1 against page, card, and overlay surfaces in both themes (WCAG 1.4.11); transparency makes contrast depend on the surface. Fill may use `bg-input/12`, but must not lower placeholder contrast below 4.5:1. Decorative borders on cards and separators still use `border`.
- Preserve semantics: destructive actions use `destructive`; amber `warn` means only warnings and stale content.
- Text has only `foreground`, `subtle-foreground`, and `muted-foreground`. Do not add transparency or opacity to `muted-foreground`: its page contrast is only slightly above 5:1 in both themes and further reduction drops below WCAG AA's 4.5:1.
- For episode, video-unit, or asset-type colors use identity tokens (`episode`, `unit-clip`, `unit-narration`, `asset-*`), with hues/colors from `episodeHue`, `unitHue`, and `assetColor`, not local calculations.

## Overlays and notifications

### Compose Dialog, Sheet, and AlertDialog as Header, Body, Footer; variable-length content belongs only in Body

Primitives limit overlay height. `DialogHeader` and `DialogFooter` stay at the ends; `DialogBody` (`SheetBody` / `AlertDialogBody`) is the only scroll region. Errors, impact explanations, and long lists in Header/Footer can push themselves and primary actions outside short windows or long translations, unreachable by scrolling. Put them in Body.

- Choose width with `size`, not call-site `max-w-*`. Multistep wizards use `size="wizard"`, sharing one `DialogBody`; reset its `scrollTop` to zero on new steps.
- Every overlay has a Title, with `sr-only` if visually hidden, so screen readers can announce its name.
- If Body can overflow and has no focusable children, give it `tabIndex={0}`, `role="region"`, and `aria-label` for keyboard scrolling.
- If Footer has a close button, pass `showCloseButton={false}` to Content to avoid duplicate exits.

### Confirm irreversible actions with AlertDialog or inline rows; reversible actions execute with an Undo toast

AlertDialog initially focuses Cancel, preventing accidental Enter from acting. Confirmation uses `variant="destructive"`; while submitting, disable both buttons and ignore close requests in `onOpenChange`, so Esc cannot close an in-flight operation.

Lightweight list-row objects, such as queued tray messages, confirm in place: replace the row with explanatory text, a `variant="destructive"` confirmation button, and `outline` Cancel. Focus Cancel. Esc or Cancel exits and restores trigger focus; leaving the confirmation area also exits. See deletion in `QueuedMessageTray`.

Recoverable remove, move, and hide operations need no confirmation. Afterwards call `pushToast(text, tone, { action: { label: t("common:undo"), onClick } })`. Undo runs the callback and closes the toast.

Toast actions can open finished results too. If a user leaves the initiating page during a long operation such as project import, show a toast with an Open project action instead of forcing navigation. After await, recheck the unmount-aborted `signal.aborted`; the write request itself does not receive this signal. Every outcome after departure (`success`, failure, confirmation required) needs a toast rather than being silently lost with the unmounted dialog. See `frontend/src/components/pages/lobby/useProjectImport.tsx`.

## Buttons

### Use `Button` from `components/ui/button` and select variants by action meaning

- `default`: primary action, at most one per region.
- `outline`: secondary actions such as Cancel and Render again.
- `ghost`: toolbar and icon buttons.
- `destructive`: irreversible delete/overwrite actions.
- `secondary`, `link`: low-emphasis actions and inline link-like actions.

Use `size`: `xs`, `sm`, `default`, `lg`; icon-only buttons use `icon`, `icon-xs`, `icon-sm`, or `icon-lg`. Icons inside buttons use `data-icon="inline-start"` or `data-icon="inline-end"`, without size classes. `Button` has no loading prop: disable it and replace the leading icon with `Loader2` with `animate-spin`.

Whole-row items (list/episode rows), thumbnail triggers, and drop zones are not action buttons. Use native `<button>` with `focus-ring`: `Button` would need call-site restyling forbidden by `no-restyle`. Navigation looking like a button uses `Link` with `buttonVariants`, not `Button` calling `navigate`, so screen readers announce a link and users can open a new tab.

## Scrolling and horizontal overflow

### Scroll heights follow the flex chain; primary columns reserve scrollbar gutters

The document itself never scrolls. Scrolling belongs only to shell-designated containers: sidebar, constrained/full shell main area, and full-bleed columns. Scroll containers must be positioned and not use viewport-height sizing; lint checks both. E2E overflow probes cover document scrolling and clipping in existing scenarios; new components and uncovered widths still need review. Check two things:

- **Height follows the flex chain.** Every flex child between shell and scroller has `min-h-0` (`min-w-0` horizontally); full-bleed section roots use `flex min-h-0 flex-1`. One missing link sets the child's minimum height to content height, stretching the scroller and leaving overflow clipped and unreachable. Do not simulate independent-column scrolling with `sticky`.
- **Primary columns whose scrollbars appear with content use `[scrollbar-gutter:stable]`.** Shell main areas already do; new main scrollers in full-bleed sections or narrow canvas breakpoints must too. Otherwise the column jumps horizontally when scrollbars appear (#3067).

Exception: short overlay lists and small scrollers not alongside content need not reserve gutters.

### Handle horizontal overflow by type: wrap long strings, fade scrolling strips, fix table columns, truncate single lines

Browsers turn `overflow-y-auto` regions (overlay bodies, canvas columns, Agent trays) into horizontal scrollers when content is too wide, rather than clipping or reporting errors. Buttons and long strings move out of view. E2E probes cover only registered scenarios; new components, long text, and narrow widths require review. Check new/changed text, button rows, and lists at the narrowest supported width, such as minimum Agent-panel width:

- **Long strings** (URLs, unspaced tokens, legacy long dialogue): use wrapping such as `wrap-break-word`. `whitespace-pre-wrap` does not force breaks.
- **Button/confirmation rows**: allow wrapping or shrinking; no unshrinkable fixed groups (#3139).
- **Horizontal strips** (tags, thumbnails): use `overflow-x-auto scroll-fade-x` to fade the clipped side.
- **Tables**: use `table-fixed` and `<colgroup>` to fix widths of short status/time/action columns with `whitespace-nowrap`; main text cells use `TruncatedText`.
- **Single-line names, paths, model IDs**: use `components/shared/TruncatedText` with parent `min-w-0`, not `title` attributes inaccessible to keyboard users. Inside focusable elements such as buttons, pass `focusable={false}` and supply full text through the outer accessible name. Render numbered-name prefixes separately without truncation.

Declare intentional horizontal scrolling with `overflow-x-auto`, recognized by probes. Undeclared horizontal scrolling is a defect.

## Edit units and saving

### Generate with `unit.saveAndGenerate` when edits are unsaved, only after a successful `save`

Edit-unit structure (fields versus actions and save-bar placement) is in “编辑单元与离开拦截” in `website/docs/dev/architecture.md`. Generation reads saved content and charges by input. With unsaved changes, label the action Save and generate (`common:save_and_generate`) and call `unit.saveAndGenerate(generate, { confirm })`. Calling `discard()` first loses edits; direct generation charges for old inputs (#3045, #3049). `confirm` covers cases such as regeneration invalidating downstream artifacts. Cancellation saves nothing; save failure generates nothing. Navigation guards cannot help because generation does not leave the unit.

Exception: generate directly when there are no unsaved changes.

### Delegate leaving and switching to the leave guard; do not add unsaved dialogs or `beforeunload` handlers

`LeaveGuardProvider` at `AppRoutes` root is the only leave guard, documented in “编辑单元与离开拦截.” Page-level dialogs or `beforeunload` duplicate prompts or block again after approval. Missing registration silently loses unsaved edits on navigation/unmount, repeatedly seen in new switches and self-managed forms (#3040, #3044, #3049, #3052, #3264). For diffs changing editable content or switching actions, verify:

- **Self-managed forms register truthfully.** Forms outside `useEditUnit` call `useLeaveGuard({ dirty, saving, save, discard })`. Save returns actual success; always returning true loses edits after failed saves. Saving reflects in-flight save/create requests; omitting it lets Discard release an already-sent save and Save and leave submit twice.
- **Non-route switches use `useConfirmLeave()`.** Previous/next storyboard, memory file, asset, and Sheet closing count. The wrapped action completes switching synchronously. Wrap multistep changes (switch view then select) together: the guard retains only the latest request.
- **Actions with secondary confirmation wrap confirmation, not opening.** For delete/remove, wrap the confirming action inside its dialog. Wrapping dialog opening needlessly loses edits if users discard first then cancel deletion. Fallible actions return `Promise<boolean>` truthfully; retain edits until success.
- **Immediately submitted fields** participate in external-removal retention with `useRetainWhile` as well as `useLeaveGuard`.
- **Non-user switches do not prompt.** Automatic positioning/navigation caused by Agent changes first checks `useHasUnsavedChanges()` and skips if dirty.
- **Navigate through wouter.** Direct `window.history` or `window.location` changes bypass guards.

Exceptions: register `allowNavigation(to)` for navigation that does not unmount the unit, such as pagination within it. Full-page refresh `beforeunload` is managed centrally by `utils/lazy-component.ts`.

### Offer actions depending on new data only after refresh settles as success

Post-write refresh uses `refreshAfterWrite` (see “项目数据刷新” in architecture docs). Undo, selecting a new entry, locating a newly created episode, and success toasts depend on refreshed data and require `success`. On refresh failure show only the refresh-failure toast, not simultaneous success while users still see old data. See `frontend/src/components/canvas/episodes/CreateEpisodeDialog.tsx`.

Exception: finishing the write itself, such as closing an editor or leaving a deleted item's page.

## Async cancellation and refetching

For data becoming stale after await, use the mechanisms below rather than manually propagating closure flags.

### Propagate cancellation across function boundaries with AbortSignal

Once an async chain crosses a function boundary (an effect calling async code or an async function making requests), use AbortSignal. A `cancelled` closure flag protects only awaits in its own function; callees still write stores or open SSE streams after await, allowing late responses to overwrite new state.

- API methods accept `options?: { signal?: AbortSignal }` and forward it unchanged to `fetch`; aborted network awaits reject automatically.
- After non-network awaits, recheck `signal.aborted` before side effects such as store writes or SSE connections, covering abort after a response has resolved.
- The owner rotates controllers: call `abort()` on the previous one before starting a new load.
- The owner handles cleanup such as loading reset. Aborted code checks `signal.aborted` in `finally` and leaves shared state untouched once obsolete, rather than interrupting replacement loading.

Exceptions: a `cancelled` flag may remain when all awaits are in one function and no callee needs cancellation, such as configuration polling in `frontend/src/router.tsx`. One-off user actions outside data loading, such as export, may finish after departure and are outside this rule.

See `frontend/src/hooks/useAssistantSession.ts` (init auto-selection and `loadSession` chain).

### Aborting requests triggered by one-time events requires refetching

After aborting an in-flight request triggered by a one-time event (SSE notification, user confirmation), the event is consumed and will not arrive again. The invalidator or new owner must initiate another fetch after abort, or the UI stays stale indefinitely. Cancellation and compensation come together: if a diff only aborts, determine who will bring the data back.

See `frontend/src/hooks/useScriptReviewDraft.ts` (invalidating and refetching during adoption writes).

### Scope changes invalidate interaction state together; async writes commit only under a valid generation

When interaction state (confirmations, in-flight markers, errors, cursors, totals) belongs to a project/session/view scope, group it in a session with a generation token. Switching scopes changes the token and resets everything; each async write checks its issued token before committing. Aborting one request or clearing a few fields leaves other late writes or uncleared state leaking into the new scope: confirmation cancels another project's task, old-view cursors reach another API, or old-session errors persist after project switches. Concurrent request sources writing shared fields use one invalidation boundary (one coordinator or token), not independent ones.

See confirmation `gen` in `frontend/src/components/usage/use-task-cancellation.ts` and the shared two-view paging coordinator in `frontend/src/stores/usage-records-store.ts`.

## Async compensation for deliberate skips

### Record skipped compensation and perform it when the protection window closes

Compensation deliberately skipped under protection (refetch, reconciliation, final switching) will not recur by itself. Record it by its trigger key; the side closing the protection window performs it. Put closure in `finally` or another success-and-failure path, or failed operations leave protection open and deferred work never runs.

### Bucket protection state by full scope and count overlapping operations

Protection surviving requests (in-flight counts, skipped flags) is bucketed by full semantic scope (request, project, session). Missing dimensions share buckets across unrelated operations: another concurrent operation releases this one's protection, or a stuck operation elsewhere blocks necessary compensation. Use counts, not booleans, for overlapping state.

See `deletingCurrentRef` (project × session counts) and `deferredRefreshRef` (recorded skipped list refresh, performed after deletion cleanup) in `frontend/src/hooks/useAssistantSession.ts`.
