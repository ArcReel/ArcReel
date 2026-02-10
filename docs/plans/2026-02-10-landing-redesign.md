# Landing Redesign Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Rebuild the ArcReel landing page into a hero-first layout with a static "discover more" grid and a compact WeChat contact dropdown.

**Architecture:** Keep routing unchanged and replace only landing composition in the React component layer. Move contact interaction logic into `LandingPage` so desktop hover and mobile click behaviors can be controlled by media-query detection and local component state. Preserve existing app shell and backend interfaces.

**Tech Stack:** React 18 + htm templates, Tailwind utility classes, custom CSS in `app.css`, Node test runner (`node:test`) for server-render snapshot assertions.

---

### Task 1: Lock requirements with failing tests

**Files:**
- Modify: `frontend/tests/landing-page.test.mjs`

**Step 1: Write the failing test**
- Assert top bar only exposes minimal actions and no legacy nav items.
- Assert hero keeps exactly one CTA.
- Assert discover section exists with static cards.
- Assert contact behavior uses dropdown copy rather than old centered modal copy.

**Step 2: Run test to verify it fails**
- Run: `node --test frontend/tests/landing-page.test.mjs`
- Expected: FAIL on legacy strings still present.

### Task 2: Rebuild landing component

**Files:**
- Modify: `frontend/src/react/components/landing-page.js`

**Step 1: Write minimal implementation**
- Remove platform value/cases/public account sections.
- Build hero + discover layout.
- Keep only one hero CTA.
- Implement WeChat dropdown with desktop hover and mobile click fallback.

**Step 2: Run targeted test**
- Run: `node --test frontend/tests/landing-page.test.mjs`
- Expected: PASS.

### Task 3: Adjust route wiring and styling

**Files:**
- Modify: `frontend/src/react/main.js`
- Modify: `frontend/src/css/app.css`

**Step 1: Update wiring**
- Remove obsolete contact modal state from `main.js`.
- Pass only required landing props.

**Step 2: Update styles**
- Add perspective grid overlay and slogan style.
- Keep pointer events disabled on background grid layers.

**Step 3: Verify tests and build**
- Run: `node --test frontend/tests/landing-page.test.mjs`
- Run: `npm run build` (workdir `frontend`)
- Expected: both pass.
