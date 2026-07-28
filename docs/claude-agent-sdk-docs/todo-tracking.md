> ## Documentation Index
> Fetch the complete documentation index at: https://code.claude.com/docs/llms.txt
> Use this file to discover all available pages before exploring further.

# Todo Lists

> Track and display todos using the Claude Agent SDK for organized task management

Todo tracking provides a structured way to manage tasks and display progress to users. The Claude Agent SDK includes built-in todo functionality that helps organize complex workflows and keep users informed about task progression.

<Note>
  `TodoWrite` is the current default in the Agent SDK and the examples on this page use it. The replacement Task tools are available now behind `CLAUDE_CODE_ENABLE_TASKS=1` and will become the default in a future release. See [Migrate to Task tools](#migrate-to-task-tools) for how monitoring code changes.
</Note>

### Todo Lifecycle

Todos follow a predictable lifecycle:

1. **Created** as `pending` when tasks are identified
2. **Activated** to `in_progress` when work begins
3. **Completed** when the task finishes successfully
4. **Removed** when all tasks in a group are completed

### When Todos Are Used

The SDK automatically creates todos for:

* **Complex multi-step tasks** requiring 3 or more distinct actions
* **User-provided task lists** when multiple items are mentioned
* **Non-trivial operations** that benefit from progress tracking
* **Explicit requests** when users ask for todo organization

## Examples

### Monitoring Todo Changes

<CodeGroup>
  ```typescript TypeScript theme={null}
  import { query } from "@anthropic-ai/claude-agent-sdk";

  for await (const message of query({
    prompt: "Optimize my React app performance and track progress with todos",
    options: { maxTurns: 15 }
  })) {
    // Todo updates are reflected in the message stream
    if (message.type === "assistant") {
      for (const block of message.message.content) {
        if (block.type === "tool_use" && block.name === "TodoWrite") {
          const todos = block.input.todos;

          console.log("Todo Status Update:");
          todos.forEach((todo, index) => {
            const status =
              todo.status === "completed" ? "✅" : todo.status === "in_progress" ? "🔧" : "❌";
            console.log(`${index + 1}. ${status} ${todo.content}`);
          });
        }
      }
    }
  }
  ```

  ```python Python theme={null}
  from claude_agent_sdk import query, ClaudeAgentOptions, AssistantMessage, ToolUseBlock

  async for message in query(
      prompt="Optimize my React app performance and track progress with todos",
      options=ClaudeAgentOptions(max_turns=15),
  ):
      # Todo updates are reflected in the message stream
      if isinstance(message, AssistantMessage):
          for block in message.content:
              if isinstance(block, ToolUseBlock) and block.name == "TodoWrite":
                  todos = block.input["todos"]

                  print("Todo Status Update:")
                  for i, todo in enumerate(todos):
                      status = (
                          "✅"
                          if todo["status"] == "completed"
                          else "🔧"
                          if todo["status"] == "in_progress"
                          else "❌"
                      )
                      print(f"{i + 1}. {status} {todo['content']}")
  ```
</CodeGroup>

### Real-time Progress Display

<CodeGroup>
  ```typescript TypeScript theme={null}
  import { query } from "@anthropic-ai/claude-agent-sdk";

  class TodoTracker {
    private todos: any[] = [];

    displayProgress() {
      if (this.todos.length === 0) return;

      const completed = this.todos.filter((t) => t.status === "completed").length;
      const inProgress = this.todos.filter((t) => t.status === "in_progress").length;
      const total = this.todos.length;

      console.log(`\nProgress: ${completed}/${total} completed`);
      console.log(`Currently working on: ${inProgress} task(s)\n`);

      this.todos.forEach((todo, index) => {
        const icon =
          todo.status === "completed" ? "✅" : todo.status === "in_progress" ? "🔧" : "❌";
        const text = todo.status === "in_progress" ? todo.activeForm : todo.content;
        console.log(`${index + 1}. ${icon} ${text}`);
      });
    }

    async trackQuery(prompt: string) {
      for await (const message of query({
        prompt,
        options: { maxTurns: 20 }
      })) {
        if (message.type === "assistant") {
          for (const block of message.message.content) {
            if (block.type === "tool_use" && block.name === "TodoWrite") {
              this.todos = block.input.todos;
              this.displayProgress();
            }
          }
        }
      }
    }
  }

  // Usage
  const tracker = new TodoTracker();
  await tracker.trackQuery("Build a complete authentication system with todos");
  ```

  ```python Python theme={null}
  from claude_agent_sdk import query, ClaudeAgentOptions, AssistantMessage, ToolUseBlock
  from typing import List, Dict


  class TodoTracker:
      def __init__(self):
          self.todos: List[Dict] = []

      def display_progress(self):
          if not self.todos:
              return

          completed = len([t for t in self.todos if t["status"] == "completed"])
          in_progress = len([t for t in self.todos if t["status"] == "in_progress"])
          total = len(self.todos)

          print(f"\nProgress: {completed}/{total} completed")
          print(f"Currently working on: {in_progress} task(s)\n")

          for i, todo in enumerate(self.todos):
              icon = (
                  "✅"
                  if todo["status"] == "completed"
                  else "🔧"
                  if todo["status"] == "in_progress"
                  else "❌"
              )
              text = (
                  todo["activeForm"]
                  if todo["status"] == "in_progress"
                  else todo["content"]
              )
              print(f"{i + 1}. {icon} {text}")

      async def track_query(self, prompt: str):
          async for message in query(prompt=prompt, options=ClaudeAgentOptions(max_turns=20)):
              if isinstance(message, AssistantMessage):
                  for block in message.content:
                      if isinstance(block, ToolUseBlock) and block.name == "TodoWrite":
                          self.todos = block.input["todos"]
                          self.display_progress()


  # Usage
  tracker = TodoTracker()
  await tracker.track_query("Build a complete authentication system with todos")
  ```
</CodeGroup>

## Migrate to Task tools

The Task tools split the single `TodoWrite` call into `TaskCreate` for each new item and `TaskUpdate` for each status change, with `TaskList` and `TaskGet` available for the model to read back the current list. Your monitoring code still inspects `tool_use` blocks in the assistant stream, but maintains a map keyed by task ID instead of replacing the whole list on every call. To opt in before the Task tools become the default, set `CLAUDE_CODE_ENABLE_TASKS=1` in `options.env`.

| With `TodoWrite`                              | With Task tools                                                                                                                                                                                                                                                                                     |
| --------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| One tool call rewrites the full `todos` array | `TaskCreate` adds one item, `TaskUpdate` patches one item by `taskId`                                                                                                                                                                                                                               |
| Match `block.name === "TodoWrite"`            | Match `block.name === "TaskCreate"` or `"TaskUpdate"`                                                                                                                                                                                                                                               |
| Item shape: `{ content, status, activeForm }` | `TaskCreate` input: `{ subject, description, activeForm?, metadata? }`. `TaskUpdate` input: `{ taskId, status?, subject?, description?, activeForm?, addBlocks?, addBlockedBy?, owner?, metadata? }`. `status` is `"pending"`, `"in_progress"`, or `"completed"`; set `status: "deleted"` to delete |
| Render `block.input.todos` directly           | Accumulate items across calls, or read a snapshot from a `TaskList` tool result                                                                                                                                                                                                                     |

The assigned task ID is not in the `TaskCreate` input. It comes back in the matching `tool_result` as `{ task: { id, subject } }`, so capture it from the result block to key your map. The following example shows the minimal change to the [Monitoring Todo Changes](#monitoring-todo-changes) loop. To render a complete list, watch for a `TaskList` tool result in the stream or accumulate `TaskCreate` results and `TaskUpdate` inputs into a map:

<CodeGroup>
  ```typescript TypeScript theme={null}
  import { query } from "@anthropic-ai/claude-agent-sdk";

  for await (const message of query({
    prompt: "Optimize my React app performance",
    options: { env: { ...process.env, CLAUDE_CODE_ENABLE_TASKS: "1" } },
  })) {
    if (message.type !== "assistant") continue;
    for (const block of message.message.content) {
      if (block.type !== "tool_use") continue;
      if (block.name === "TaskCreate") {
        const input = block.input as { subject: string };
        console.log(`+ ${input.subject}`);
      } else if (block.name === "TaskUpdate") {
        const input = block.input as { taskId: string; status?: string };
        if (input.status) console.log(`  ${input.taskId} -> ${input.status}`);
      }
    }
  }
  ```

  ```python Python theme={null}
  from claude_agent_sdk import query, ClaudeAgentOptions, AssistantMessage, ToolUseBlock

  async for message in query(
      prompt="Optimize my React app performance",
      options=ClaudeAgentOptions(env={"CLAUDE_CODE_ENABLE_TASKS": "1"}),
  ):
      if not isinstance(message, AssistantMessage):
          continue
      for block in message.content:
          if not isinstance(block, ToolUseBlock):
              continue
          if block.name == "TaskCreate":
              print(f"+ {block.input['subject']}")
          elif block.name == "TaskUpdate" and block.input.get("status"):
              print(f"  {block.input['taskId']} -> {block.input['status']}")
  ```
</CodeGroup>

## Related Documentation

* [TypeScript SDK Reference](/en/agent-sdk/typescript)
* [Python SDK Reference](/en/agent-sdk/python)
* [Streaming vs Single Mode](/en/agent-sdk/streaming-vs-single-mode)
* [Custom Tools](/en/agent-sdk/custom-tools)
