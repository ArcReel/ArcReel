# Assistant Tool Approval Flow Proposal (Issue 1)

## Background

Currently `can_use_tool` only routes `AskUserQuestion` through user interaction; all other tools default to `allow`. This means the web client has no visibility or control over high-risk tool calls (such as `Bash`, `Write`, `Edit`), which is inconsistent with the official recommendation to "have the application layer accept approval requests and return decisions".

## Goals

1. All non-auto-approved tool requests are shown in the frontend as "pending approval".
2. Users can make three types of decisions: `Allow / Deny / Allow with modified parameters`.
3. Decision results flow back to `can_use_tool` without interrupting the current streaming session.
4. Approval requests survive reconnection (approval state is not lost after a page refresh or SSE reconnect).

## Proposed Architecture

## 1) Unified Event Model

Add a new runtime message type (proposed):

- `tool_approval_request`
  - `request_id`
  - `tool_name`
  - `input`
  - `created_at`
  - `session_id`
  - `risk_level` (optional, for frontend-level display)

Add a new frontend SSE event (proposed):

- `approval`: carries `tool_approval_request`

> Could reuse the `question` event channel, but a separate event type is recommended to avoid semantic confusion between `AskUserQuestion` and tool approvals.

## 2) SessionManager Changes

In `_build_can_use_tool_callback`:

1. Detect `AskUserQuestion` (keep existing logic).
2. For other tools:
   - If the tool matches an "auto-approve rule", immediately return `PermissionResultAllow`.
   - Otherwise, create a `pending_approval`, write a `tool_approval_request` to the message buffer, and `await` the user decision.
3. After the user decision arrives:
   - `allow` -> `PermissionResultAllow(updated_input=...)`
   - `deny` -> `PermissionResultDeny(message=..., interrupt=...)`

Add `ManagedSession.pending_approvals` and corresponding methods:

- `add_pending_approval()`
- `resolve_pending_approval()`
- `cancel_pending_approvals()`
- `get_pending_approval_payloads()`

## 3) AssistantService / Snapshot Changes

Add to the `snapshot` response:

- `pending_approvals: []`

The first SSE reconnect packet (`snapshot`) should include unresolved approval items so the frontend can restore the approval UI.

## 4) Router Changes

Add a new endpoint (proposed):

- `POST /api/v1/assistant/sessions/{id}/approvals/{request_id}/decision`
  - Request body:
    - `decision`: `allow | deny`
    - `updated_input` (optional)
    - `message` (denial reason, optional)
    - `interrupt` (optional)

## 5) Frontend Changes

Add to `use-assistant-state`:

- `assistantPendingApproval`
- `assistantApproving`
- `handleApproveToolRequest(requestId, decisionPayload)`

Add an approval card area to `AssistantMessageArea`:

1. Show tool name, key parameter summary, and risk label.
2. Support allow / deny.
3. Support advanced mode for editing `updated_input` (JSON).

## Default Policy Recommendation

Auto-approve by default (configurable):

- `Read`
- `Glob`
- `Grep`
- `LS`

Require approval by default:

- `Bash`
- `Write`
- `Edit`
- `MultiEdit`
- Any other tools with side effects or external access

## Compatibility and Migration

1. Phase 1: Backend adds support for `pending_approvals` and decision API; frontend rolls out gradually.
2. Phase 2: Disable "non-AskUserQuestion all-default-allow" behavior.
3. Phase 3: Add rule configuration (per-project / per-session).

## Acceptance Checklist

1. When `Bash` is triggered, the frontend shows an approval card instead of silently executing.
2. After clicking "Deny", the model receives the rejection feedback and continues reasoning.
3. After a page refresh, unhandled approvals are still restored from `snapshot.pending_approvals`.
4. The streaming session is not interrupted; `status` events still converge on `ResultMessage`.
5. The `AskUserQuestion` flow does not regress.
