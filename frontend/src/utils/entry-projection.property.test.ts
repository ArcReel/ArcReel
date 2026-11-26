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
  flag: fc.boolean(),
});

const streamArbitrary = fc.array(event, { minLength: 1, maxLength: maxSteps, size: "max" }).map((events) => {
  const tools: string[] = [];
  return events.map(({ kind, name, reference, nested, flag }, seq): TimelineEntry => {
    const target = tools[reference % (tools.length + 1)] ?? "ghost";
    const base = { seq, uuid: `e-${seq}`, parent_tool_use_id: nested ? target : undefined };
    const text = String(seq);
    switch (kind) {
      case "user":
      case "assistant":
        return { ...base, type: kind, content: [{ type: "text", text }], message_id: flag ? `msg-${reference}` : undefined };
      case "tool_use": {
        const id = `tu-${seq}`;
        tools.push(id);
        return { ...base, type: "assistant", content: [{ type: "tool_use", name, id, input: {} }] };
      }
      case "tool_result":
        return { ...base, type: kind, tool_use_id: target, content: text, is_error: flag };
      case "question_answer":
        return { ...base, type: "user", subtype: kind, tool_use_id: target, content: text, answers: { q: text }, is_error: flag };
      case "skill_invocation":
        return { ...base, type: "system", subtype: kind, tool_use_id: target, skill_name: "demo", skill_args: text };
      default:
        return { ...base, type: "system", subtype: kind, task_id: `t-${reference}`, tool_use_id: target,
          description: text, summary: flag ? text : undefined, task_status: flag ? "completed" : undefined,
          usage: flag ? { total_tokens: seq } : undefined };
    }
  });
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
