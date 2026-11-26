import * as fc from "fast-check";
import { expect, it } from "vitest";
import { maxSteps, propertyParameters, propertyTimeout } from "@/test/property";
import type { TimelineEntry } from "@/types";
import { createTimelineProjector, projectEntriesToTurns } from "./entry-projection";

const event = fc.record({
  kind: fc.constantFrom("user", "assistant", "tool_use", "tool_result", "question_answer",
    "task_started", "task_progress", "task_notification", "interrupt", "noise", "skill_invocation"),
  name: fc.constantFrom("Agent", "Skill", "Read", "AskUserQuestion"),
  reference: fc.nat(5),
  nested: fc.boolean(),
  hasMessageId: fc.boolean(),
  isError: fc.boolean(),
  hasAnswers: fc.boolean(),
  hasToolUseId: fc.boolean(),
  hasSummary: fc.boolean(),
  isCompleted: fc.boolean(),
  hasUsage: fc.boolean(),
});

const streamArbitrary = fc.array(event, { minLength: 1, maxLength: maxSteps, size: "max" }).map((events) => {
  const tools: string[] = [];
  return events.map(
    ({ kind, name, reference, nested, hasMessageId, isError, hasAnswers, hasToolUseId, hasSummary, isCompleted, hasUsage }, seq): TimelineEntry => {
      const target = tools[reference % (tools.length + 1)] ?? "ghost";
      const base = { seq, uuid: `e-${seq}`, parent_tool_use_id: nested ? target : undefined };
      const text = String(seq);
      switch (kind) {
        case "user":
        case "assistant":
          return { ...base, type: kind, content: [{ type: "text", text }], message_id: hasMessageId ? `msg-${reference}` : undefined };
        case "tool_use": {
          const id = `tu-${seq}`;
          tools.push(id);
          return { ...base, type: "assistant", content: [{ type: "tool_use", name, id, input: {} }] };
        }
        case "tool_result":
          return { ...base, type: kind, tool_use_id: target, content: text, is_error: isError };
        case "question_answer":
          return { ...base, type: "user", subtype: kind, tool_use_id: target, content: text, answers: hasAnswers ? { q: text } : undefined, is_error: isError };
        case "skill_invocation":
          return { ...base, type: "system", subtype: kind, tool_use_id: hasToolUseId ? target : null, skill_name: "demo", skill_args: text };
        case "interrupt":
        case "noise":
          return { ...base, type: "system", subtype: kind };
        default:
          return { ...base, type: "system", subtype: kind, task_id: `t-${reference}`, tool_use_id: target,
            description: text, summary: hasSummary ? text : undefined, task_status: isCompleted ? "completed" : undefined,
            usage: hasUsage ? { total_tokens: seq } : undefined };
      }
    },
  );
});

it("matches full timeline replay after every appended event", { timeout: propertyTimeout }, () => {
  fc.assert(fc.property(streamArbitrary, (stream) => {
    const projector = createTimelineProjector();
    for (let length = 1; length <= stream.length; length++) {
      const prefix = stream.slice(0, length);
      expect(projector.project(prefix)).toEqual(projectEntriesToTurns(prefix));
    }
  }), propertyParameters("src/utils/entry-projection.property.test.ts"));
});
