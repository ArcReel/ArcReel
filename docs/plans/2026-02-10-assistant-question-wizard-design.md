# Assistant Question Step-by-Step Wizard Interaction Design (Confirmed)

## Goal
Change the current "display all questions at once + submit all at the bottom" pattern to a "step-by-step question wizard", resolving the UX issue of "unclear whether selected answers have been submitted". Without changing the backend protocol, make users clearly understand the current progress, next action, and final submission timing.

## Confirmed Decisions
- Use "frontend step-by-step wizard + submit all at the final question".
- Navigation: both single and multiple choice advance by clicking "Next Question"; no auto-submit.
- Allow going back to edit: provide "Previous" and preserve already-filled content.
- Layout: horizontal progress bar at the top + current question content below.
- Final question action: display "Finish & Submit" button.

## Current State and Constraints
- Existing backend endpoint `POST /assistant/sessions/{session_id}/questions/{question_id}/answer` receives a batch of `answers`; does not support per-question submission.
- Frontend implementation currently lives in `frontend/src/react/pages/assistant-page.js` and already supports:
  - Single/multiple choice answer state management.
  - "Other" option supplemental input.
  - Unified answer assembly and submission.
- Conclusion: this iteration is primarily a UI/interaction refactor; backend interface and data structure remain unchanged.

## Approach Comparison
1. Frontend step-by-step wizard + submit all at the final question (Recommended)
   - Pros: minimal changes, low risk, fully compatible with current backend.
   - Cons: no server-side draft mid-way; user must re-answer if page is refreshed (local cache can be added later).

2. Step-by-step wizard + per-step draft to backend + final submit
   - Pros: recoverable, more fault-tolerant.
   - Cons: requires new draft protocol and storage; out of scope for this iteration.

3. True per-question conversational submission
   - Pros: strongest conversational feel.
   - Cons: significant changes to protocol and agent behavior; not suitable for this quick optimization round.

## Interaction Design
### Top Progress Bar
- Show a horizontal progress bar at the top of the "Your Choices Needed" area; nodes are `1..N`.
- Node labels prefer `question.header`; show `Question x` if absent.
- States:
  - Current question: highlighted.
  - Already visited: clickable to go back.
  - Not yet visited: displayed only, no jumping forward allowed.
- Mobile supports horizontal scrolling to avoid compressing question and option areas.

### Current Question Card
- Only render one question at a time.
- Maintain existing visual style: question stem, single/multiple choice label, option descriptions, "Other" input.
- Fixed bottom action area:
  - Left: `Previous` (disabled on first question).
  - Right: `Next Question`; switches to `Finish & Submit` on the last question.
- Show progress text: `Question i/N`.

## State and Data Flow
New/reused state (frontend):
- `currentQuestionIndex`: current question index.
- `questionAnswers`: question answers (single-choice string / multi-choice array).
- `questionCustomAnswers`: "Other" input content.
- `visitedSteps`: visited steps (for progress bar styling and back-navigation control).

Key flow:
1. When a new `assistantPendingQuestion` (or `question_id` change) is received, reset state and position to question 1.
2. `Next Question` becomes available only after the user selects an answer for the current question; clicking it only advances the index, does not call the submit API.
3. User can go back via `Previous` or the progress bar to edit; already-filled answers are preserved.
4. Clicking `Finish & Submit` on the last question triggers the existing batch assembly and submission logic.

## Submission and Error Handling
- Validate all before `Finish & Submit`:
  - Every question must be answered.
  - If "Other" is selected, corresponding text input is required.
- During submission:
  - Disable step switching, back navigation, and submit button.
  - Button label shows `Submitting...`.
- Submission failure:
  - Retain current question and all answers; do not clear state.
  - Show error message; allow inline retry.
- Submission success:
  - Reuse existing behavior to clear pending question; collapse the Q&A section.

## Testing Strategy
Add frontend test file: `frontend/tests/assistant-question-wizard.test.mjs`, covering:
1. Only current question is shown; progress bar highlights correctly.
2. "Next Question" disabled when unanswered; enabled after answering.
3. Clicking "Next Question" does not trigger API submission.
4. "Previous" can go back and answers are preserved.
5. Last question button label is "Finish & Submit".
6. Final submit API is called exactly once; payload matches existing format.
7. Submission failure does not lose answers; submission success clears pending.
8. On small screens, progress bar is horizontally scrollable.

## Implementation Scope
- Primary modification: `frontend/src/react/pages/assistant-page.js`
- Possible minor adjustment: `frontend/src/css/app.css` (if progress bar styles need additions)
- State hook: `frontend/src/react/hooks/use-assistant-state.js` retains the existing batch submission protocol; no interface changes

## Acceptance Criteria
- Users can clearly identify the current question number, total questions, and final submission timing.
- No more "selected but unclear if submitted" UX confusion.
- Existing backend Q&A interface remains compatible; no additional protocol changes.
- Does not affect regular chat sending, session switching, session interruption, or other existing functionality.
