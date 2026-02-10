# Configure permissions

Control how your agent uses tools with permission modes, hooks, and declarative allow/deny rules.

---

The Claude Agent SDK provides permission controls to manage how Claude uses tools. Use permission modes and rules to define what's allowed automatically, and the [`canUseTool` callback](/docs/en/agent-sdk/user-input) to handle everything else at runtime.

<Note>
This page covers permission modes and rules. To build interactive approval flows where users approve or deny tool requests at runtime, see [Handle approvals and user input](/docs/en/agent-sdk/user-input).
</Note>

## How permissions are evaluated

When Claude requests a tool, the SDK checks permissions in this order:

<Steps>
  <Step title="Hooks">
    Run [hooks](/docs/en/agent-sdk/hooks) first, which can allow, deny, or continue to the next step
  </Step>
  <Step title="Permission rules">
    Check rules defined in [settings.json](https://code.claude.com/docs/en/settings#permission-settings) in this order: `deny` rules first (block regardless of other rules), then `allow` rules (permit if matched), then `ask` rules (prompt for approval). These declarative rules let you pre-approve, block, or require approval for specific tools without writing code.
  </Step>
  <Step title="Permission mode">
    Apply the active [permission mode](#permission-modes) (`bypassPermissions`, `acceptEdits`, `dontAsk`, etc.)
  </Step>
  <Step title="canUseTool callback">
    If not resolved by rules or modes, call your [`canUseTool` callback](/docs/en/agent-sdk/user-input) for a decision
  </Step>
</Steps>

![Permission evaluation flow diagram](/docs/images/agent-sdk/permissions-flow.svg)

This page focuses on **permission modes** (step 3), the static configuration that controls default behavior. For the other steps:

- **Hooks**: run custom code to allow, deny, or modify tool requests. See [Control execution with hooks](/docs/en/agent-sdk/hooks).
- **Permission rules**: configure declarative allow/deny rules in `settings.json`. See [Permission settings](https://code.claude.com/docs/en/settings#permission-settings).
- **canUseTool callback**: prompt users for approval at runtime. See [Handle approvals and user input](/docs/en/agent-sdk/user-input).

## Permission modes

Permission modes provide global control over how Claude uses tools. You can set the permission mode when calling `query()` or change it dynamically during streaming sessions.

### Available modes

The SDK supports these permission modes:

| Mode | Description | Tool behavior |
| :--- | :---------- | :------------ |
| `default` | Standard permission behavior | No auto-approvals; unmatched tools trigger your `canUseTool` callback |
| `acceptEdits` | Auto-accept file edits | File edits and [filesystem operations](#accept-edits-mode-acceptedits) (`mkdir`, `rm`, `mv`, etc.) are automatically approved |
| `bypassPermissions` | Bypass all permission checks | All tools run without permission prompts (use with caution) |
| `plan` | Planning mode | No tool execution; Claude plans without making changes |

<Warning>
**Subagent inheritance**: When using `bypassPermissions`, all subagents inherit this mode and it cannot be overridden. Subagents may have different system prompts and less constrained behavior than your main agent. Enabling `bypassPermissions` grants them full, autonomous system access without any approval prompts.
</Warning>

### Set permission mode

You can set the permission mode once when starting a query, or change it dynamically while the session is active.

<Tabs>
  <Tab title="At query time">
    Pass `permission_mode` (Python) or `permissionMode` (TypeScript) when creating a query. This mode applies for the entire session unless changed dynamically.

    <CodeGroup>

    ```python Python
    import asyncio
    from claude_agent_sdk import query, ClaudeAgentOptions

    async def main():
        async for message in query(
            prompt="Help me refactor this code",
            options=ClaudeAgentOptions(
                permission_mode="default",  # Set the mode here
            ),
        ):
            if hasattr(message, "result"):
                print(message.result)

    asyncio.run(main())
    ```

    ```typescript TypeScript
    import { query } from "@anthropic-ai/claude-agent-sdk";

    async function main() {
      for await (const message of query({
        prompt: "Help me refactor this code",
        options: {
          permissionMode: "default",  // Set the mode here
        },
      })) {
        if ("result" in message) {
          console.log(message.result);
        }
      }
    }

    main();
    ```

    </CodeGroup>
  </Tab>
  <Tab title="During streaming">
    Call `set_permission_mode()` (Python) or `setPermissionMode()` (TypeScript) to change the mode mid-session. The new mode takes effect immediately for all subsequent tool requests. This lets you start restrictive and loosen permissions as trust builds, for example switching to `acceptEdits` after reviewing Claude's initial approach.

    <CodeGroup>

    ```python Python
    import asyncio
    from claude_agent_sdk import query, ClaudeAgentOptions

    async def main():
        q = query(
            prompt="Help me refactor this code",
            options=ClaudeAgentOptions(
                permission_mode="default",  # Start in default mode
            ),
        )

        # Change mode dynamically mid-session
        await q.set_permission_mode("acceptEdits")

        # Process messages with the new permission mode
        async for message in q:
            if hasattr(message, "result"):
                print(message.result)

    asyncio.run(main())
    ```

    ```typescript TypeScript
    import { query } from "@anthropic-ai/claude-agent-sdk";

    async function main() {
      const q = query({
        prompt: "Help me refactor this code",
        options: {
          permissionMode: "default",  // Start in default mode
        },
      });

      // Change mode dynamically mid-session
      await q.setPermissionMode("acceptEdits");

      // Process messages with the new permission mode
      for await (const message of q) {
        if ("result" in message) {
          console.log(message.result);
        }
      }
    }

    main();
    ```

    </CodeGroup>
  </Tab>
</Tabs>

### Mode details

#### Accept edits mode (`acceptEdits`)

Auto-approves file operations so Claude can edit code without prompting. Other tools (like Bash commands that aren't filesystem operations) still require normal permissions.

**Auto-approved operations:**
- File edits (Edit, Write tools)
- Filesystem commands: `mkdir`, `touch`, `rm`, `mv`, `cp`

**Use when:** you trust Claude's edits and want faster iteration, such as during prototyping or when working in an isolated directory.

#### Bypass permissions mode (`bypassPermissions`)

Auto-approves all tool uses without prompts. Hooks still execute and can block operations if needed.

<Warning>
Use with extreme caution. Claude has full system access in this mode. Only use in controlled environments where you trust all possible operations.
</Warning>

#### Plan mode (`plan`)

Prevents tool execution entirely. Claude can analyze code and create plans but cannot make changes. Claude may use `AskUserQuestion` to clarify requirements before finalizing the plan. See [Handle approvals and user input](/docs/en/agent-sdk/user-input#handle-clarifying-questions) for handling these prompts.

**Use when:** you want Claude to propose changes without executing them, such as during code review or when you need to approve changes before they're made.

## Related resources

For the other steps in the permission evaluation flow:

- [Handle approvals and user input](/docs/en/agent-sdk/user-input): interactive approval prompts and clarifying questions
- [Hooks guide](/docs/en/agent-sdk/hooks): run custom code at key points in the agent lifecycle
- [Permission rules](https://code.claude.com/docs/en/settings#permission-settings): declarative allow/deny rules in `settings.json`