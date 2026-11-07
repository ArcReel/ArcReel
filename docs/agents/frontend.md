# Frontend primitives and test stubs

Read this document before installing or first using primitives from `components/ui/`, writing tests for primitives, or stubbing outbound calls. Review rules for primitive usage are in `docs/standards/frontend.md`.

## Installing primitives

The change that first uses a primitive runs `pnpm exec shadcn add <组件>` (using the `shadcn` version pinned in `package.json`) and commits the dependencies introduced by the CLI together. Do not preinstall primitives that are not needed yet; knip reports unused files and dependencies. A group of primitives that needs coordinated adaptation (such as overlays) may be installed before its consumers. Temporarily list unreferenced files in `knip.jsonc`'s `ignore`, with a comment requiring the first consumer to remove the entry. Do not use `--overwrite` on installed components: component source comes from the online registry, so pinning the package version does not pin component content, and overwriting discards local changes.

## Base UI and Radix differences

Most shadcn documentation and community examples use Radix. This repository's primitives use Base UI (`style` is `base-nova` in `components.json`). Type checking cannot detect these differences:

- A `modal` Popover must contain a Close inside its Popup.
- Portal renders an extra `<div>`; adjust selectors and styles that depend on DOM hierarchy.
- Combobox and Select popups open asynchronously after a click. In tests, wait with `findByRole("listbox")`, `findByRole("option")`, or similar. The Combobox search input is inside the popup, and its trigger has `role="combobox"`.
- Checkbox and Radio render as `span` elements with roles. Disabled state is `aria-disabled="true"`, so `toBeDisabled()` does not apply.
- Collapsible unmounts panel content when closed. Click its trigger before querying panel elements; read the trigger's `aria-expanded` for expanded state.
- Slider thumbs cannot be found by role in jsdom. Retain a native `<input type="range">` for single-value sliders that need role-based tests.

For components not listed above, test keyboard, focus, and closing behavior in the browser before first use.

## Primitive styling

`no-restyle` allows layout classes only: put spacing, borders, and colors on an ordinary wrapper outside the primitive, and use only margin on the primitive to separate it from that wrapper. Write a collapsible trigger as `<CollapsibleTrigger render={<Button variant="ghost" size="sm" />}>`; rotate its expansion icon with `group-aria-expanded/button:rotate-90`.

When `render` receives a primitive such as `Button`, check classes against that component's contract. When it receives a native element or a non-primitive such as `Link`, check them against the outer primitive. Unstyled triggers and `Collapsible` are registered in `UNSTYLED_PRIMITIVES` in `eslint.config.js` and have no class-category restriction; other primitives still allow layout classes only.

## Test stubs

Route new outbound calls through the `API` class so tests can stub them at this boundary with `vi.spyOn(API, method)`. Tests of `api.ts` itself use handwritten fetch / Response stubs. Neither unit tests nor page-level suites (recorded-fixture replay, see `docs/standards/testing.md`) introduce msw. For SSE, return `FakeSseStream` from `src/test/fakeSseStream.ts` through spies on `API.openProjectEventStream` / `API.openAssistantEntriesStream`; `openSseStream` and the streaming wrappers in `api.ts` use `src/test/fakeSseFetch.ts`.
