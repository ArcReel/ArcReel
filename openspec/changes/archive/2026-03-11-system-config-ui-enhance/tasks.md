## 1. Design Phase (Using /frontend-design)

- [x] 1.1 Use the `/frontend-design` skill to design the visual style of the four top navigation tabs (including dot badge and tab active state)
- [x] 1.2 Use the `/frontend-design` skill to design the two states of the `TabSaveFooter` component: normal embedded state (disabled) and sticky highlighted state (has unsaved changes)

## 2. Reusable Component: TabSaveFooter

- [x] 2.1 Create the `TabSaveFooter` component accepting `isDirty`, `saving`, `error`, `onSave`, `onReset` props
- [x] 2.2 Implement sticky logic: when `isDirty` is true, add `sticky bottom-0 z-10 shadow` styles; save button switches to primary highlighted color
- [x] 2.3 Add "Revert" button, displayed when `isDirty` is true; click triggers `onReset`
- [x] 2.4 When `saving` is true, show loading state and disable button; when `error` is non-empty, show error message next to the button

## 3. Configuration Tab Components

- [x] 3.1 Create the `AgentConfigTab` component (ArcReel Agent Configuration), maintaining draft state for Anthropic-related fields internally, with `TabSaveFooter` at the bottom
- [x] 3.2 Create the `MediaConfigTab` component (AI Image/Video Generation Configuration), maintaining draft state for Gemini/Vertex-related fields internally, with `TabSaveFooter` at the bottom
- [x] 3.3 Create the `AdvancedConfigTab` component (Advanced Configuration), maintaining draft state for rate-limiting/concurrency fields internally, with `TabSaveFooter` at the bottom
- [x] 3.4 Each configuration tab component implements `isDirty` detection (`deepEqual` comparison of draft with saved values in `useRef`)

## 4. Top Navigation Tabs and Badges

- [x] 4.1 Change top navigation tabs from `[config, api-keys]` to `[agent, media, advanced, api-keys]`, keeping `ApiKeysTab` component unchanged
- [x] 4.2 Implement tab dot badges: when a configuration tab has unsaved changes, a dot (●) appears next to its label; the badge continues to show after switching tabs

## 5. Unified Clear Buttons

- [x] 5.1 Add clear (×) buttons uniformly to all optional fields (base_url, API keys, and other non-required fields), shown when there is a value and hidden when empty
- [x] 5.2 After clicking clear, the field value is set to empty and the corresponding tab's `isDirty` update is triggered

## 6. SystemConfigPage Integration and Cleanup

- [x] 6.1 Refactor `SystemConfigPage` as an orchestration layer combining the four tab components; remove original global draft state and bottom global save button
- [x] 6.2 Retain the Connection Test functionality to ensure it still works correctly (assigned to the corresponding tab)
- [x] 6.3 Add sufficient `padding-bottom` to the page bottom to prevent the sticky footer from blocking the last row of content

## 7. Missing Required Configuration Detection and Warning

- [x] 7.1 Implement the `getConfigIssues(config)` utility function: separately checks `anthropic_api_key.is_set`, image backend credentials (`gemini_api_key.is_set` or `vertex_credentials.is_set`), and video backend credentials; outputs `ConfigIssue[]` and deduplicates/merges entries where image/video point to the same provider with the same reason; encapsulate request and caching in the `useConfigStatus` hook, exposing `issues: ConfigIssue[]` and `isComplete: boolean`
- [x] 7.2 Add red dot badge to the settings button in `ProjectsPage.tsx` (near line 358), shown when `isConfigComplete === false`
- [x] 7.3 Add red dot badge to the settings button in `GlobalHeader.tsx` (near line 323), shown when `isConfigComplete === false`
- [x] 7.4 Add warning banner component above tab navigation in `SystemConfigPage`, listing missing required items with links to jump to the corresponding tab
- [x] 7.5 After a configuration tab saves successfully, trigger `useConfigStatus` to re-check, ensuring badges and banners update in real-time

## 8. Quality Assurance (Using /vercel-react-best-practices)

- [x] 8.1 Use the `/vercel-react-best-practices` skill to review the component implementation, ensuring compliance with React best practices (useRef tracking savedValues to avoid unnecessary re-renders, deepEqual caching, etc.)
- [x] 8.2 Run `pnpm typecheck` to confirm no TypeScript type errors
- [x] 8.3 Manual verification: after modifying fields, sticky footer appears + tab badge shows; after saving, sticky releases; after switching tabs, badge remains; after reverting, original values restored; clear button works correctly
- [x] 8.4 Manual verification of missing configuration warnings: when unconfigured, settings button shows red dot and settings page shows banner; after completing configuration and saving, badge and banner disappear in real-time
