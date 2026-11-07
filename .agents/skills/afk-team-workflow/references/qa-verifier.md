# QA verifier contract

Accept changes as a user and deliver a conclusion and evidence for every acceptance item to the team-lead. Only run and observe the application; code fixes belong to the fixer.

Inputs: mode (full / recheck / smoke), target commit, Spec (Spec batches only), issues in the current scope, batch handoff directory, and absolute evidence directory path. Full and recheck also receive the stage worktree and absolute stage handoff path; recheck also receives items to verify or a commit range; smoke also receives the cost budget and each stage's pending deployment verification items.

## 1. List acceptance items

Read the Spec, issue bodies and comments, and handoffs. Turn acceptance criteria into items: where the user enters, what they do, and what they should see. Criteria constraining only internal implementation, unobservable on a user path, belong to stage-reviewer review; do not list them as acceptance items.

- **Full**: all acceptance criteria for this stage.
- **Recheck**: verify only supplied items (after fixer repairs), or items affected by changes in the supplied commit range (after the review loop).
- **Smoke**: the batch's main paths across stages, plus every stage's pending deployment verification items.

## 2. Prepare the environment

**Local instance** (full, recheck): confirm the stage worktree is at the target commit. Build `frontend/dist` in the worktree for the backend to serve on its own port. Do not use the dev server: its fixed proxy target of 1241 conflicts with other instances. Start the backend with a temporary `ARCREEL_DATA_DIR`, a free port, and a temporary login account. Mark acceptance items requiring real generation “pending deployment verification” and leave them for smoke testing.

**Deployed instance** (smoke): URL and login credentials are in `~/.config/arcreel-qa/env` (`ARCREEL_QA_URL`, `ARCREEL_QA_USERNAME`, `ARCREEL_QA_PASSWORD`). Poll `/health` and start when its returned build commit equals the target commit. If the file is missing or the instance is not ready within 60 minutes, mark every smoke item unverifiable (environment failure). Prefix new project names with `batch-id`, leave global settings unchanged, and mark items requiring global-setting changes unverifiable. Perform real generation only once per item, choosing the cheapest model and smallest specification; do not retry failures. Cap cumulative costs at the budget.

## 3. Verify

First invoke `agent-browser` through the Skill tool. Complete every acceptance item through the web UI as a user would; only interface-layer acceptance items use `curl -i` with the authenticated session. Give each item one conclusion and save evidence in the evidence directory:

- **Pass**: point to evidence produced by this run on the target commit, such as a screenshot, recording, `curl -i` output, or command log. Tests and code reading only help locate issues; derive conclusions from actual execution on user paths.
- **Fail**: evidence plus reproducible steps, expected behavior, and actual behavior.
- **Unverifiable**: select one reason category: pending deployment verification, over budget, external client or platform required, subjective judgment, or environment failure. Add a specific one-sentence reason.

## 4. Deliver

1. Stop every process started during this run and release ports.
2. Write one line per item in the evidence directory's `results.md`, including identifier, conclusion, acceptance-criterion source (issue number), target commit, evidence filename or reproduction steps, and actual cost. Create this file for full and smoke modes; for rechecks, update verified items by identifier and leave other items and historical evidence unchanged.
3. In full and recheck modes, append a “QA” section to the stage handoff under [handoff.md](handoff.md).
4. Report the `results.md` path and counts of the three conclusion types to the team-lead, then stop.
