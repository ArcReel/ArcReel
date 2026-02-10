# Handling stop reasons

Detect refusals and other stop reasons directly from result messages in the Agent SDK

---

The `stop_reason` field on result messages tells you why the model stopped generating. This is the recommended way to detect refusals, max-token limits, and other termination conditions (no stream parsing required).

<Tip>
`stop_reason` is available on every `ResultMessage`, regardless of whether streaming is enabled. You don't need to set `include_partial_messages` (Python) or `includePartialMessages` (TypeScript).
</Tip>

## Reading stop_reason

The `stop_reason` field is present on both success and error result messages. Check it after iterating through the message stream:

<CodeGroup>

```python Python
from claude_agent_sdk import query, ResultMessage
import asyncio

async def check_stop_reason():
    async for message in query(prompt="Write a poem about the ocean"):
        if isinstance(message, ResultMessage):
            print(f"Stop reason: {message.stop_reason}")
            if message.stop_reason == "refusal":
                print("The model declined this request.")

asyncio.run(check_stop_reason())
```

```typescript TypeScript
import { query } from "@anthropic-ai/claude-agent-sdk";

for await (const message of query({
  prompt: "Write a poem about the ocean",
})) {
  if (message.type === "result") {
    console.log("Stop reason:", message.stop_reason);
    if (message.stop_reason === "refusal") {
      console.log("The model declined this request.");
    }
  }
}
```

</CodeGroup>

## Available stop reasons

| Stop reason | Meaning |
|:------------|:--------|
| `end_turn` | The model finished generating its response normally. |
| `max_tokens` | The response reached the maximum output token limit. |
| `stop_sequence` | The model generated a configured stop sequence. |
| `refusal` | The model declined to fulfill the request. |
| `tool_use` | The model's final output was a tool call. This is uncommon in SDK results because tool calls are normally executed before the result is returned. |
| `null` | No API response was received; for example, an error occurred before the first request, or the result was replayed from a cached session. |

## Stop reasons on error results

Error results (such as `error_max_turns` or `error_during_execution`) also carry `stop_reason`. The value reflects the last assistant message received before the error occurred:

| Result variant | `stop_reason` value |
|:---------------|:-------------------|
| `success` | The stop reason from the final assistant message. |
| `error_max_turns` | The stop reason from the last assistant message before the turn limit was hit. |
| `error_max_budget_usd` | The stop reason from the last assistant message before the budget was exceeded. |
| `error_max_structured_output_retries` | The stop reason from the last assistant message before the retry limit was hit. |
| `error_during_execution` | The last stop reason seen, or `null` if the error occurred before any API response. |

<CodeGroup>

```python Python
from claude_agent_sdk import query, ClaudeAgentOptions, ResultMessage
import asyncio

async def handle_max_turns():
    options = ClaudeAgentOptions(max_turns=3)

    async for message in query(prompt="Refactor this module", options=options):
        if isinstance(message, ResultMessage):
            if message.subtype == "error_max_turns":
                print(f"Hit turn limit. Last stop reason: {message.stop_reason}")
                # stop_reason might be "end_turn" or "tool_use"
                # depending on what the model was doing when the limit hit

asyncio.run(handle_max_turns())
```

```typescript TypeScript
import { query } from "@anthropic-ai/claude-agent-sdk";

for await (const message of query({
  prompt: "Refactor this module",
  options: { maxTurns: 3 },
})) {
  if (message.type === "result" && message.subtype === "error_max_turns") {
    console.log("Hit turn limit. Last stop reason:", message.stop_reason);
    // stop_reason might be "end_turn" or "tool_use"
    // depending on what the model was doing when the limit hit
  }
}
```

</CodeGroup>

## Detecting refusals

`stop_reason === "refusal"` is the simplest way to detect when the model declines a request. Previously, detecting refusals required enabling partial message streaming and manually scanning `StreamEvent` messages for `message_delta` events. With `stop_reason` on the result message, you can check directly:

<CodeGroup>

```python Python
from claude_agent_sdk import query, ResultMessage
import asyncio

async def safe_query(prompt: str):
    async for message in query(prompt=prompt):
        if isinstance(message, ResultMessage):
            if message.stop_reason == "refusal":
                print("Request was declined. Please revise your prompt.")
                return None
            return message.result
    return None

asyncio.run(safe_query("Summarize this article"))
```

```typescript TypeScript
import { query } from "@anthropic-ai/claude-agent-sdk";

async function safeQuery(prompt: string): Promise<string | null> {
  for await (const message of query({ prompt })) {
    if (message.type === "result") {
      if (message.stop_reason === "refusal") {
        console.log("Request was declined. Please revise your prompt.");
        return null;
      }
      if (message.subtype === "success") {
        return message.result;
      }
      return null;
    }
  }
  return null;
}
```

</CodeGroup>

## Next steps

- [Stream responses in real-time](/docs/en/agent-sdk/streaming-output): access raw API events including `message_delta` as they arrive
- [Structured outputs](/docs/en/agent-sdk/structured-outputs): get typed JSON responses from the agent
- [Tracking costs and usage](/docs/en/agent-sdk/cost-tracking): understand token usage and billing from result messages