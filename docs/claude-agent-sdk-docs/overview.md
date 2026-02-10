# Agent SDK overview

Build production AI agents with Claude Code as a library

---

<Note>
The Claude Code SDK has been renamed to the Claude Agent SDK. If you're migrating from the old SDK, see the [Migration Guide](/docs/en/agent-sdk/migration-guide).
</Note>

Build AI agents that autonomously read files, run commands, search the web, edit code, and more. The Agent SDK gives you the same tools, agent loop, and context management that power Claude Code, programmable in Python and TypeScript.

<CodeGroup>
```python Python
import asyncio
from claude_agent_sdk import query, ClaudeAgentOptions

async def main():
    async for message in query(
        prompt="Find and fix the bug in auth.py",
        options=ClaudeAgentOptions(allowed_tools=["Read", "Edit", "Bash"])
    ):
        print(message)  # Claude reads the file, finds the bug, edits it

asyncio.run(main())
```

```typescript TypeScript
import { query } from "@anthropic-ai/claude-agent-sdk";

for await (const message of query({
  prompt: "Find and fix the bug in auth.py",
  options: { allowedTools: ["Read", "Edit", "Bash"] }
})) {
  console.log(message);  // Claude reads the file, finds the bug, edits it
}
```
</CodeGroup>

The Agent SDK includes built-in tools for reading files, running commands, and editing code, so your agent can start working immediately without you implementing tool execution. Dive into the quickstart or explore real agents built with the SDK:

<CardGroup cols={2}>
  <Card title="Quickstart" icon="play" href="/docs/en/agent-sdk/quickstart">
    Build a bug-fixing agent in minutes
  </Card>
  <Card title="Example agents" icon="star" href="https://github.com/anthropics/claude-agent-sdk-demos">
    Email assistant, research agent, and more
  </Card>
</CardGroup>

## Get started

<Steps>
  <Step title="Install the SDK">
    <Tabs>
      <Tab title="TypeScript">
        ```bash
        npm install @anthropic-ai/claude-agent-sdk
        ```
      </Tab>
      <Tab title="Python">
        ```bash
        pip install claude-agent-sdk
        ```
      </Tab>
    </Tabs>
  </Step>
  <Step title="Set your API key">
    Get an API key from the [Console](https://platform.claude.com/), then set it as an environment variable:

    ```bash
    export ANTHROPIC_API_KEY=your-api-key
    ```

    The SDK also supports authentication via third-party API providers:

    - **Amazon Bedrock**: set `CLAUDE_CODE_USE_BEDROCK=1` environment variable and configure AWS credentials
    - **Google Vertex AI**: set `CLAUDE_CODE_USE_VERTEX=1` environment variable and configure Google Cloud credentials
    - **Microsoft Azure**: set `CLAUDE_CODE_USE_FOUNDRY=1` environment variable and configure Azure credentials

    See the setup guides for [Bedrock](https://code.claude.com/docs/en/amazon-bedrock), [Vertex AI](https://code.claude.com/docs/en/google-vertex-ai), or [Azure AI Foundry](https://code.claude.com/docs/en/azure-ai-foundry) for details.

    <Note>
    Unless previously approved, Anthropic does not allow third party developers to offer claude.ai login or rate limits for their products, including agents built on the Claude Agent SDK. Please use the API key authentication methods described in this document instead.
    </Note>
  </Step>
  <Step title="Run your first agent">
    This example creates an agent that lists files in your current directory using built-in tools.

    <CodeGroup>
    ```python Python
    import asyncio
    from claude_agent_sdk import query, ClaudeAgentOptions

    async def main():
        async for message in query(
            prompt="What files are in this directory?",
            options=ClaudeAgentOptions(allowed_tools=["Bash", "Glob"])
        ):
            if hasattr(message, "result"):
                print(message.result)

    asyncio.run(main())
    ```

    ```typescript TypeScript
    import { query } from "@anthropic-ai/claude-agent-sdk";

    for await (const message of query({
      prompt: "What files are in this directory?",
      options: { allowedTools: ["Bash", "Glob"] },
    })) {
      if ("result" in message) console.log(message.result);
    }
    ```
    </CodeGroup>
  </Step>
</Steps>

**Ready to build?** Follow the [Quickstart](/docs/en/agent-sdk/quickstart) to create an agent that finds and fixes bugs in minutes.

## Capabilities

Everything that makes Claude Code powerful is available in the SDK:

<Tabs>
  <Tab title="Built-in tools">
    Your agent can read files, run commands, and search codebases out of the box. Key tools include:

    | Tool | What it does |
    |------|--------------|
    | **Read** | Read any file in the working directory |
    | **Write** | Create new files |
    | **Edit** | Make precise edits to existing files |
    | **Bash** | Run terminal commands, scripts, git operations |
    | **Glob** | Find files by pattern (`**/*.ts`, `src/**/*.py`) |
    | **Grep** | Search file contents with regex |
    | **WebSearch** | Search the web for current information |
    | **WebFetch** | Fetch and parse web page content |
    | **[AskUserQuestion](/docs/en/agent-sdk/user-input#handle-clarifying-questions)** | Ask the user clarifying questions with multiple choice options |

    This example creates an agent that searches your codebase for TODO comments:

    <CodeGroup>
    ```python Python
    import asyncio
    from claude_agent_sdk import query, ClaudeAgentOptions

    async def main():
        async for message in query(
            prompt="Find all TODO comments and create a summary",
            options=ClaudeAgentOptions(allowed_tools=["Read", "Glob", "Grep"])
        ):
            if hasattr(message, "result"):
                print(message.result)

    asyncio.run(main())
    ```

    ```typescript TypeScript
    import { query } from "@anthropic-ai/claude-agent-sdk";

    for await (const message of query({
      prompt: "Find all TODO comments and create a summary",
      options: { allowedTools: ["Read", "Glob", "Grep"] }
    })) {
      if ("result" in message) console.log(message.result);
    }
    ```
    </CodeGroup>

  </Tab>
  <Tab title="Hooks">
    Run custom code at key points in the agent lifecycle. SDK hooks use callback functions to validate, log, block, or transform agent behavior.

    **Available hooks:** `PreToolUse`, `PostToolUse`, `Stop`, `SessionStart`, `SessionEnd`, `UserPromptSubmit`, and more.

    This example logs all file changes to an audit file:

    <CodeGroup>
    ```python Python
    import asyncio
    from datetime import datetime
    from claude_agent_sdk import query, ClaudeAgentOptions, HookMatcher

    async def log_file_change(input_data, tool_use_id, context):
        file_path = input_data.get('tool_input', {}).get('file_path', 'unknown')
        with open('./audit.log', 'a') as f:
            f.write(f"{datetime.now()}: modified {file_path}\n")
        return {}

    async def main():
        async for message in query(
            prompt="Refactor utils.py to improve readability",
            options=ClaudeAgentOptions(
                permission_mode="acceptEdits",
                hooks={
                    "PostToolUse": [HookMatcher(matcher="Edit|Write", hooks=[log_file_change])]
                }
            )
        ):
            if hasattr(message, "result"):
                print(message.result)

    asyncio.run(main())
    ```

    ```typescript TypeScript
    import { query, HookCallback } from "@anthropic-ai/claude-agent-sdk";
    import { appendFileSync } from "fs";

    const logFileChange: HookCallback = async (input) => {
      const filePath = (input as any).tool_input?.file_path ?? "unknown";
      appendFileSync("./audit.log", `${new Date().toISOString()}: modified ${filePath}\n`);
      return {};
    };

    for await (const message of query({
      prompt: "Refactor utils.py to improve readability",
      options: {
        permissionMode: "acceptEdits",
        hooks: {
          PostToolUse: [{ matcher: "Edit|Write", hooks: [logFileChange] }]
        }
      }
    })) {
      if ("result" in message) console.log(message.result);
    }
    ```
    </CodeGroup>

    [Learn more about hooks →](/docs/en/agent-sdk/hooks)
  </Tab>
  <Tab title="Subagents">
    Spawn specialized agents to handle focused subtasks. Your main agent delegates work, and subagents report back with results.

    Define custom agents with specialized instructions. Include `Task` in `allowedTools` since subagents are invoked via the Task tool:

    <CodeGroup>
    ```python Python
    import asyncio
    from claude_agent_sdk import query, ClaudeAgentOptions, AgentDefinition

    async def main():
        async for message in query(
            prompt="Use the code-reviewer agent to review this codebase",
            options=ClaudeAgentOptions(
                allowed_tools=["Read", "Glob", "Grep", "Task"],
                agents={
                    "code-reviewer": AgentDefinition(
                        description="Expert code reviewer for quality and security reviews.",
                        prompt="Analyze code quality and suggest improvements.",
                        tools=["Read", "Glob", "Grep"]
                    )
                }
            )
        ):
            if hasattr(message, "result"):
                print(message.result)

    asyncio.run(main())
    ```

    ```typescript TypeScript
    import { query } from "@anthropic-ai/claude-agent-sdk";

    for await (const message of query({
      prompt: "Use the code-reviewer agent to review this codebase",
      options: {
        allowedTools: ["Read", "Glob", "Grep", "Task"],
        agents: {
          "code-reviewer": {
            description: "Expert code reviewer for quality and security reviews.",
            prompt: "Analyze code quality and suggest improvements.",
            tools: ["Read", "Glob", "Grep"]
          }
        }
      }
    })) {
      if ("result" in message) console.log(message.result);
    }
    ```
    </CodeGroup>

    Messages from within a subagent's context include a `parent_tool_use_id` field, letting you track which messages belong to which subagent execution.

    [Learn more about subagents →](/docs/en/agent-sdk/subagents)
  </Tab>
  <Tab title="MCP">
    Connect to external systems via the Model Context Protocol: databases, browsers, APIs, and [hundreds more](https://github.com/modelcontextprotocol/servers).

    This example connects the [Playwright MCP server](https://github.com/microsoft/playwright-mcp) to give your agent browser automation capabilities:

    <CodeGroup>
    ```python Python
    import asyncio
    from claude_agent_sdk import query, ClaudeAgentOptions

    async def main():
        async for message in query(
            prompt="Open example.com and describe what you see",
            options=ClaudeAgentOptions(
                mcp_servers={
                    "playwright": {"command": "npx", "args": ["@playwright/mcp@latest"]}
                }
            )
        ):
            if hasattr(message, "result"):
                print(message.result)

    asyncio.run(main())
    ```

    ```typescript TypeScript
    import { query } from "@anthropic-ai/claude-agent-sdk";

    for await (const message of query({
      prompt: "Open example.com and describe what you see",
      options: {
        mcpServers: {
          playwright: { command: "npx", args: ["@playwright/mcp@latest"] }
        }
      }
    })) {
      if ("result" in message) console.log(message.result);
    }
    ```
    </CodeGroup>

    [Learn more about MCP →](/docs/en/agent-sdk/mcp)
  </Tab>
  <Tab title="Permissions">
    Control exactly which tools your agent can use. Allow safe operations, block dangerous ones, or require approval for sensitive actions.

    <Note>
    For interactive approval prompts and the `AskUserQuestion` tool, see [Handle approvals and user input](/docs/en/agent-sdk/user-input).
    </Note>

    This example creates a read-only agent that can analyze but not modify code:

    <CodeGroup>
    ```python Python
    import asyncio
    from claude_agent_sdk import query, ClaudeAgentOptions

    async def main():
        async for message in query(
            prompt="Review this code for best practices",
            options=ClaudeAgentOptions(
                allowed_tools=["Read", "Glob", "Grep"],
                permission_mode="bypassPermissions"
            )
        ):
            if hasattr(message, "result"):
                print(message.result)

    asyncio.run(main())
    ```

    ```typescript TypeScript
    import { query } from "@anthropic-ai/claude-agent-sdk";

    for await (const message of query({
      prompt: "Review this code for best practices",
      options: {
        allowedTools: ["Read", "Glob", "Grep"],
        permissionMode: "bypassPermissions"
      }
    })) {
      if ("result" in message) console.log(message.result);
    }
    ```
    </CodeGroup>

    [Learn more about permissions →](/docs/en/agent-sdk/permissions)
  </Tab>
  <Tab title="Sessions">
    Maintain context across multiple exchanges. Claude remembers files read, analysis done, and conversation history. Resume sessions later, or fork them to explore different approaches.

    This example captures the session ID from the first query, then resumes to continue with full context:

    <CodeGroup>
    ```python Python
    import asyncio
    from claude_agent_sdk import query, ClaudeAgentOptions

    async def main():
        session_id = None

        # First query: capture the session ID
        async for message in query(
            prompt="Read the authentication module",
            options=ClaudeAgentOptions(allowed_tools=["Read", "Glob"])
        ):
            if hasattr(message, 'subtype') and message.subtype == 'init':
                session_id = message.session_id

        # Resume with full context from the first query
        async for message in query(
            prompt="Now find all places that call it",  # "it" = auth module
            options=ClaudeAgentOptions(resume=session_id)
        ):
            if hasattr(message, "result"):
                print(message.result)

    asyncio.run(main())
    ```

    ```typescript TypeScript
    import { query } from "@anthropic-ai/claude-agent-sdk";

    let sessionId: string | undefined;

    // First query: capture the session ID
    for await (const message of query({
      prompt: "Read the authentication module",
      options: { allowedTools: ["Read", "Glob"] }
    })) {
      if (message.type === "system" && message.subtype === "init") {
        sessionId = message.session_id;
      }
    }

    // Resume with full context from the first query
    for await (const message of query({
      prompt: "Now find all places that call it",  // "it" = auth module
      options: { resume: sessionId }
    })) {
      if ("result" in message) console.log(message.result);
    }
    ```
    </CodeGroup>

    [Learn more about sessions →](/docs/en/agent-sdk/sessions)
  </Tab>
</Tabs>

### Claude Code features

The SDK also supports Claude Code's filesystem-based configuration. To use these features, set `setting_sources=["project"]` (Python) or `settingSources: ['project']` (TypeScript)  in your options.

| Feature | Description | Location |
|---------|-------------|----------|
| [Skills](/docs/en/agent-sdk/skills) | Specialized capabilities defined in Markdown | `.claude/skills/SKILL.md` |
| [Slash commands](/docs/en/agent-sdk/slash-commands) | Custom commands for common tasks | `.claude/commands/*.md` |
| [Memory](/docs/en/agent-sdk/modifying-system-prompts) | Project context and instructions | `CLAUDE.md` or `.claude/CLAUDE.md` |
| [Plugins](/docs/en/agent-sdk/plugins) | Extend with custom commands, agents, and MCP servers | Programmatic via `plugins` option |

## Compare the Agent SDK to other Claude tools

The Claude platform offers multiple ways to build with Claude. Here's how the Agent SDK fits in:

<Tabs>
  <Tab title="Agent SDK vs Client SDK">
    The [Anthropic Client SDK](/docs/en/api/client-sdks) gives you direct API access: you send prompts and implement tool execution yourself. The **Agent SDK** gives you Claude with built-in tool execution.

    With the Client SDK, you implement a tool loop. With the Agent SDK, Claude handles it:

    <CodeGroup>
    ```python Python
    # Client SDK: You implement the tool loop
    response = client.messages.create(...)
    while response.stop_reason == "tool_use":
        result = your_tool_executor(response.tool_use)
        response = client.messages.create(tool_result=result, ...)

    # Agent SDK: Claude handles tools autonomously
    async for message in query(prompt="Fix the bug in auth.py"):
        print(message)
    ```

    ```typescript TypeScript
    // Client SDK: You implement the tool loop
    let response = await client.messages.create({...});
    while (response.stop_reason === "tool_use") {
      const result = yourToolExecutor(response.tool_use);
      response = await client.messages.create({ tool_result: result, ... });
    }

    // Agent SDK: Claude handles tools autonomously
    for await (const message of query({ prompt: "Fix the bug in auth.py" })) {
      console.log(message);
    }
    ```
    </CodeGroup>
  </Tab>
  <Tab title="Agent SDK vs Claude Code CLI">
    Same capabilities, different interface:

    | Use case | Best choice |
    |----------|-------------|
    | Interactive development | CLI |
    | CI/CD pipelines | SDK |
    | Custom applications | SDK |
    | One-off tasks | CLI |
    | Production automation | SDK |

    Many teams use both: CLI for daily development, SDK for production. Workflows translate directly between them.
  </Tab>
</Tabs>

## Changelog

View the full changelog for SDK updates, bug fixes, and new features:

- **TypeScript SDK**: [view CHANGELOG.md](https://github.com/anthropics/claude-agent-sdk-typescript/blob/main/CHANGELOG.md)
- **Python SDK**: [view CHANGELOG.md](https://github.com/anthropics/claude-agent-sdk-python/blob/main/CHANGELOG.md)

## Reporting bugs

If you encounter bugs or issues with the Agent SDK:

- **TypeScript SDK**: [report issues on GitHub](https://github.com/anthropics/claude-agent-sdk-typescript/issues)
- **Python SDK**: [report issues on GitHub](https://github.com/anthropics/claude-agent-sdk-python/issues)

## Branding guidelines

For partners integrating the Claude Agent SDK, use of Claude branding is optional. When referencing Claude in your product:

**Allowed:**
- "Claude Agent" (preferred for dropdown menus)
- "Claude" (when within a menu already labeled "Agents")
- "{YourAgentName} Powered by Claude" (if you have an existing agent name)

**Not permitted:**
- "Claude Code" or "Claude Code Agent"
- Claude Code-branded ASCII art or visual elements that mimic Claude Code

Your product should maintain its own branding and not appear to be Claude Code or any Anthropic product. For questions about branding compliance, contact our [sales team](https://www.anthropic.com/contact-sales).

## License and terms

Use of the Claude Agent SDK is governed by [Anthropic's Commercial Terms of Service](https://www.anthropic.com/legal/commercial-terms), including when you use it to power products and services that you make available to your own customers and end users, except to the extent a specific component or dependency is covered by a different license as indicated in that component's LICENSE file.

## Next steps

<CardGroup cols={2}>
  <Card title="Quickstart" icon="play" href="/docs/en/agent-sdk/quickstart">
    Build an agent that finds and fixes bugs in minutes
  </Card>
  <Card title="Example agents" icon="star" href="https://github.com/anthropics/claude-agent-sdk-demos">
    Email assistant, research agent, and more
  </Card>
  <Card title="TypeScript SDK" icon="code" href="/docs/en/agent-sdk/typescript">
    Full TypeScript API reference and examples
  </Card>
  <Card title="Python SDK" icon="code" href="/docs/en/agent-sdk/python">
    Full Python API reference and examples
  </Card>
</CardGroup>