# Rewind file changes with checkpointing

Track file changes during agent sessions and restore files to any previous state

---

File checkpointing tracks file modifications made through the Write, Edit, and NotebookEdit tools during an agent session, allowing you to rewind files to any previous state. Want to try it out? Jump to the [interactive example](#try-it-out).

With checkpointing, you can:

- **Undo unwanted changes** by restoring files to a known good state
- **Explore alternatives** by restoring to a checkpoint and trying a different approach
- **Recover from errors** when the agent makes incorrect modifications

<Warning>
Only changes made through the Write, Edit, and NotebookEdit tools are tracked. Changes made through Bash commands (like `echo > file.txt` or `sed -i`) are not captured by the checkpoint system.
</Warning>

## How checkpointing works

When you enable file checkpointing, the SDK creates backups of files before modifying them through the Write, Edit, or NotebookEdit tools. User messages in the response stream include a checkpoint UUID that you can use as a restore point. 

Checkpoint works with these built-in tools that the agent uses to modify files:

| Tool | Description |
|------|-------------|
| Write | Creates a new file or overwrites an existing file with new content |
| Edit | Makes targeted edits to specific parts of an existing file |
| NotebookEdit | Modifies cells in Jupyter notebooks (`.ipynb` files) |

<Note>
File rewinding restores files on disk to a previous state. It does not rewind the conversation itself. The conversation history and context remain intact after calling `rewindFiles()` (TypeScript) or `rewind_files()` (Python).
</Note>

The checkpoint system tracks:

- Files created during the session
- Files modified during the session
- The original content of modified files

When you rewind to a checkpoint, created files are deleted and modified files are restored to their content at that point.

## Implement checkpointing

To use file checkpointing, enable it in your options, capture checkpoint UUIDs from the response stream, then call `rewindFiles()` (TypeScript) or `rewind_files()` (Python) when you need to restore.

The following example shows the complete flow: enable checkpointing, capture the checkpoint UUID and session ID from the response stream, then resume the session later to rewind files. Each step is explained in detail below.

<CodeGroup>

```python Python
import asyncio
import os
from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions, UserMessage, ResultMessage

async def main():
    # Step 1: Enable checkpointing
    options = ClaudeAgentOptions(
        enable_file_checkpointing=True,
        permission_mode="acceptEdits",  # Auto-accept file edits without prompting
        extra_args={"replay-user-messages": None},  # Required to receive checkpoint UUIDs in the response stream
        env={**os.environ, "CLAUDE_CODE_ENABLE_SDK_FILE_CHECKPOINTING": "1"}
    )

    checkpoint_id = None
    session_id = None

    # Run the query and capture checkpoint UUID and session ID
    async with ClaudeSDKClient(options) as client:
        await client.query("Refactor the authentication module")

        # Step 2: Capture checkpoint UUID from the first user message
        async for message in client.receive_response():
            if isinstance(message, UserMessage) and message.uuid and not checkpoint_id:
                checkpoint_id = message.uuid
            if isinstance(message, ResultMessage) and not session_id:
                session_id = message.session_id

    # Step 3: Later, rewind by resuming the session with an empty prompt
    if checkpoint_id and session_id:
        async with ClaudeSDKClient(ClaudeAgentOptions(
            enable_file_checkpointing=True,
            resume=session_id
        )) as client:
            await client.query("")  # Empty prompt to open the connection
            async for message in client.receive_response():
                await client.rewind_files(checkpoint_id)
                break
        print(f"Rewound to checkpoint: {checkpoint_id}")

asyncio.run(main())
```

```typescript TypeScript
import { query } from "@anthropic-ai/claude-agent-sdk";

async function main() {
  // Step 1: Enable checkpointing
  const opts = {
    enableFileCheckpointing: true,
    permissionMode: "acceptEdits" as const,  // Auto-accept file edits without prompting
    extraArgs: { 'replay-user-messages': null },  // Required to receive checkpoint UUIDs in the response stream
    env: { ...process.env, CLAUDE_CODE_ENABLE_SDK_FILE_CHECKPOINTING: '1' }
  };

  const response = query({
    prompt: "Refactor the authentication module",
    options: opts
  });

  let checkpointId: string | undefined;
  let sessionId: string | undefined;

  // Step 2: Capture checkpoint UUID from the first user message
  for await (const message of response) {
    if (message.type === 'user' && message.uuid && !checkpointId) {
      checkpointId = message.uuid;
    }
    if ('session_id' in message && !sessionId) {
      sessionId = message.session_id;
    }
  }

  // Step 3: Later, rewind by resuming the session with an empty prompt
  if (checkpointId && sessionId) {
    const rewindQuery = query({
      prompt: "",  // Empty prompt to open the connection
      options: { ...opts, resume: sessionId }
    });

    for await (const msg of rewindQuery) {
      await rewindQuery.rewindFiles(checkpointId);
      break;
    }
    console.log(`Rewound to checkpoint: ${checkpointId}`);
  }
}

main();
```

</CodeGroup>

<Steps>

<Step title="Set the environment variable">

File checkpointing requires the `CLAUDE_CODE_ENABLE_SDK_FILE_CHECKPOINTING` environment variable. You can set it either via command line before running your script, or directly in the SDK options.

**Option 1: Set via command line**

```bash Python
export CLAUDE_CODE_ENABLE_SDK_FILE_CHECKPOINTING=1
```

**Option 2: Set in SDK options**

Pass the environment variable through the `env` option when configuring the SDK:

<CodeGroup>

```python Python
import os

options = ClaudeAgentOptions(
    enable_file_checkpointing=True,
    env={**os.environ, "CLAUDE_CODE_ENABLE_SDK_FILE_CHECKPOINTING": "1"}
)
```

```typescript TypeScript
const opts = {
  enableFileCheckpointing: true,
  env: { ...process.env, CLAUDE_CODE_ENABLE_SDK_FILE_CHECKPOINTING: '1' }
};
```

</CodeGroup>

</Step>

<Step title="Enable checkpointing">

Configure your SDK options to enable checkpointing and receive checkpoint UUIDs:

| Option | Python | TypeScript | Description |
|--------|--------|------------|-------------|
| Enable checkpointing | `enable_file_checkpointing=True` | `enableFileCheckpointing: true` | Tracks file changes for rewinding |
| Receive checkpoint UUIDs | `extra_args={"replay-user-messages": None}` | `extraArgs: { 'replay-user-messages': null }` | Required to get user message UUIDs in the stream |

<CodeGroup>

```python Python
options = ClaudeAgentOptions(
    enable_file_checkpointing=True,
    permission_mode="acceptEdits",
    extra_args={"replay-user-messages": None}
)

async with ClaudeSDKClient(options) as client:
    await client.query("Refactor the authentication module")
```

```typescript TypeScript
const response = query({
  prompt: "Refactor the authentication module",
  options: {
    enableFileCheckpointing: true,
    permissionMode: "acceptEdits" as const,
    extraArgs: { 'replay-user-messages': null }
  }
});
```

</CodeGroup>

</Step>

<Step title="Capture checkpoint UUID and session ID">

With the `replay-user-messages` option set (shown above), each user message in the response stream has a UUID that serves as a checkpoint.

For most use cases, capture the first user message UUID (`message.uuid`); rewinding to it restores all files to their original state. To store multiple checkpoints and rewind to intermediate states, see [Multiple restore points](#multiple-restore-points).

Capturing the session ID (`message.session_id`) is optional; you only need it if you want to rewind later, after the stream completes. If you're calling `rewindFiles()` immediately while still processing messages (as the example in [Checkpoint before risky operations](#checkpoint-before-risky-operations) does), you can skip capturing the session ID.

<CodeGroup>

```python Python
checkpoint_id = None
session_id = None

async for message in client.receive_response():
    # Update checkpoint on each user message (keeps the latest)
    if isinstance(message, UserMessage) and message.uuid:
        checkpoint_id = message.uuid
    # Capture session ID from the result message
    if isinstance(message, ResultMessage):
        session_id = message.session_id
```

```typescript TypeScript
let checkpointId: string | undefined;
let sessionId: string | undefined;

for await (const message of response) {
  // Update checkpoint on each user message (keeps the latest)
  if (message.type === 'user' && message.uuid) {
    checkpointId = message.uuid;
  }
  // Capture session ID from any message that has it
  if ('session_id' in message) {
    sessionId = message.session_id;
  }
}
```

</CodeGroup>

</Step>

<Step title="Rewind files">

To rewind after the stream completes, resume the session with an empty prompt and call `rewind_files()` (Python) or `rewindFiles()` (TypeScript) with your checkpoint UUID. You can also rewind during the stream; see [Checkpoint before risky operations](#checkpoint-before-risky-operations) for that pattern.

<CodeGroup>

```python Python
async with ClaudeSDKClient(ClaudeAgentOptions(
    enable_file_checkpointing=True,
    resume=session_id
)) as client:
    await client.query("")  # Empty prompt to open the connection
    async for message in client.receive_response():
        await client.rewind_files(checkpoint_id)
        break
```

```typescript TypeScript
const rewindQuery = query({
  prompt: "",  // Empty prompt to open the connection
  options: { ...opts, resume: sessionId }
});

for await (const msg of rewindQuery) {
  await rewindQuery.rewindFiles(checkpointId);
  break;
}
```

</CodeGroup>

If you capture the session ID and checkpoint ID, you can also rewind from the CLI:

```bash
claude --resume <session-id> --rewind-files <checkpoint-uuid>
```

</Step>

</Steps>

## Common patterns

These patterns show different ways to capture and use checkpoint UUIDs depending on your use case.

### Checkpoint before risky operations

This pattern keeps only the most recent checkpoint UUID, updating it before each agent turn. If something goes wrong during processing, you can immediately rewind to the last safe state and break out of the loop.

<CodeGroup>

```python Python
import asyncio
import os
from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions, UserMessage

async def main():
    options = ClaudeAgentOptions(
        enable_file_checkpointing=True,
        permission_mode="acceptEdits",
        extra_args={"replay-user-messages": None},
        env={**os.environ, "CLAUDE_CODE_ENABLE_SDK_FILE_CHECKPOINTING": "1"}
    )

    safe_checkpoint = None

    async with ClaudeSDKClient(options) as client:
        await client.query("Refactor the authentication module")

        async for message in client.receive_response():
            # Update checkpoint before each agent turn starts
            # This overwrites the previous checkpoint. Only keep the latest
            if isinstance(message, UserMessage) and message.uuid:
                safe_checkpoint = message.uuid

            # Decide when to revert based on your own logic
            # For example: error detection, validation failure, or user input
            if your_revert_condition and safe_checkpoint:
                await client.rewind_files(safe_checkpoint)
                # Exit the loop after rewinding, files are restored
                break

asyncio.run(main())
```

```typescript TypeScript
import { query } from "@anthropic-ai/claude-agent-sdk";

async function main() {
  const response = query({
    prompt: "Refactor the authentication module",
    options: {
      enableFileCheckpointing: true,
      permissionMode: "acceptEdits" as const,
      extraArgs: { 'replay-user-messages': null },
      env: { ...process.env, CLAUDE_CODE_ENABLE_SDK_FILE_CHECKPOINTING: '1' }
    }
  });

  let safeCheckpoint: string | undefined;

  for await (const message of response) {
    // Update checkpoint before each agent turn starts
    // This overwrites the previous checkpoint. Only keep the latest
    if (message.type === 'user' && message.uuid) {
      safeCheckpoint = message.uuid;
    }

    // Decide when to revert based on your own logic
    // For example: error detection, validation failure, or user input
    if (yourRevertCondition && safeCheckpoint) {
      await response.rewindFiles(safeCheckpoint);
      // Exit the loop after rewinding, files are restored
      break;
    }
  }
}

main();
```

</CodeGroup>

### Multiple restore points

If Claude makes changes across multiple turns, you might want to rewind to a specific point rather than all the way back. For example, if Claude refactors a file in turn one and adds tests in turn two, you might want to keep the refactor but undo the tests.

This pattern stores all checkpoint UUIDs in an array with metadata. After the session completes, you can rewind to any previous checkpoint:

<CodeGroup>

```python Python
import asyncio
import os
from dataclasses import dataclass
from datetime import datetime
from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions, UserMessage, ResultMessage

# Store checkpoint metadata for better tracking
@dataclass
class Checkpoint:
    id: str
    description: str
    timestamp: datetime

async def main():
    options = ClaudeAgentOptions(
        enable_file_checkpointing=True,
        permission_mode="acceptEdits",
        extra_args={"replay-user-messages": None},
        env={**os.environ, "CLAUDE_CODE_ENABLE_SDK_FILE_CHECKPOINTING": "1"}
    )

    checkpoints = []
    session_id = None

    async with ClaudeSDKClient(options) as client:
        await client.query("Refactor the authentication module")

        async for message in client.receive_response():
            if isinstance(message, UserMessage) and message.uuid:
                checkpoints.append(Checkpoint(
                    id=message.uuid,
                    description=f"After turn {len(checkpoints) + 1}",
                    timestamp=datetime.now()
                ))
            if isinstance(message, ResultMessage) and not session_id:
                session_id = message.session_id

    # Later: rewind to any checkpoint by resuming the session
    if checkpoints and session_id:
        target = checkpoints[0]  # Pick any checkpoint
        async with ClaudeSDKClient(ClaudeAgentOptions(
            enable_file_checkpointing=True,
            resume=session_id
        )) as client:
            await client.query("")  # Empty prompt to open the connection
            async for message in client.receive_response():
                await client.rewind_files(target.id)
                break
        print(f"Rewound to: {target.description}")

asyncio.run(main())
```

```typescript TypeScript
import { query } from "@anthropic-ai/claude-agent-sdk";

// Store checkpoint metadata for better tracking
interface Checkpoint {
  id: string;
  description: string;
  timestamp: Date;
}

async function main() {
  const opts = {
    enableFileCheckpointing: true,
    permissionMode: "acceptEdits" as const,
    extraArgs: { 'replay-user-messages': null },
    env: { ...process.env, CLAUDE_CODE_ENABLE_SDK_FILE_CHECKPOINTING: '1' }
  };

  const response = query({
    prompt: "Refactor the authentication module",
    options: opts
  });

  const checkpoints: Checkpoint[] = [];
  let sessionId: string | undefined;

  for await (const message of response) {
    if (message.type === 'user' && message.uuid) {
      checkpoints.push({
        id: message.uuid,
        description: `After turn ${checkpoints.length + 1}`,
        timestamp: new Date()
      });
    }
    if ('session_id' in message && !sessionId) {
      sessionId = message.session_id;
    }
  }

  // Later: rewind to any checkpoint by resuming the session
  if (checkpoints.length > 0 && sessionId) {
    const target = checkpoints[0];  // Pick any checkpoint
    const rewindQuery = query({
      prompt: "",  // Empty prompt to open the connection
      options: { ...opts, resume: sessionId }
    });

    for await (const msg of rewindQuery) {
      await rewindQuery.rewindFiles(target.id);
      break;
    }
    console.log(`Rewound to: ${target.description}`);
  }
}

main();
```

</CodeGroup>

## Try it out

This complete example creates a small utility file, has the agent add documentation comments, shows you the changes, then asks if you want to rewind.

Before you begin, make sure you have the [Claude Agent SDK installed](/docs/en/agent-sdk/quickstart).

<Steps>

<Step title="Create a test file">

Create a new file called `utils.py` (Python) or `utils.ts` (TypeScript) and paste the following code:

<CodeGroup>

```python utils.py
def add(a, b):
    return a + b

def subtract(a, b):
    return a - b

def multiply(a, b):
    return a * b

def divide(a, b):
    if b == 0:
        raise ValueError("Cannot divide by zero")
    return a / b
```

```typescript utils.ts
export function add(a: number, b: number): number {
  return a + b;
}

export function subtract(a: number, b: number): number {
  return a - b;
}

export function multiply(a: number, b: number): number {
  return a * b;
}

export function divide(a: number, b: number): number {
  if (b === 0) {
    throw new Error("Cannot divide by zero");
  }
  return a / b;
}
```

</CodeGroup>

</Step>

<Step title="Run the interactive example">

Create a new file called `try_checkpointing.py` (Python) or `try_checkpointing.ts` (TypeScript) in the same directory as your utility file, and paste the following code.

This script asks Claude to add doc comments to your utility file, then gives you the option to rewind and restore the original.

<CodeGroup>

```python try_checkpointing.py
import asyncio
from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions, UserMessage, ResultMessage

async def main():
    # Configure the SDK with checkpointing enabled
    # - enable_file_checkpointing: Track file changes for rewinding
    # - permission_mode: Auto-accept file edits without prompting
    # - extra_args: Required to receive user message UUIDs in the stream
    options = ClaudeAgentOptions(
        enable_file_checkpointing=True,
        permission_mode="acceptEdits",
        extra_args={"replay-user-messages": None}
    )

    checkpoint_id = None  # Store the user message UUID for rewinding
    session_id = None     # Store the session ID for resuming

    print("Running agent to add doc comments to utils.py...\n")

    # Run the agent and capture checkpoint data from the response stream
    async with ClaudeSDKClient(options) as client:
        await client.query("Add doc comments to utils.py")

        async for message in client.receive_response():
            # Capture the first user message UUID - this is our restore point
            if isinstance(message, UserMessage) and message.uuid and not checkpoint_id:
                checkpoint_id = message.uuid
            # Capture the session ID so we can resume later
            if isinstance(message, ResultMessage):
                session_id = message.session_id

    print("Done! Open utils.py to see the added doc comments.\n")

    # Ask the user if they want to rewind the changes
    if checkpoint_id and session_id:
        response = input("Rewind to remove the doc comments? (y/n): ")

        if response.lower() == "y":
            # Resume the session with an empty prompt, then rewind
            async with ClaudeSDKClient(ClaudeAgentOptions(
                enable_file_checkpointing=True,
                resume=session_id
            )) as client:
                await client.query("")  # Empty prompt opens the connection
                async for message in client.receive_response():
                    await client.rewind_files(checkpoint_id)  # Restore files
                    break

            print("\n✓ File restored! Open utils.py to verify the doc comments are gone.")
        else:
            print("\nKept the modified file.")

asyncio.run(main())
```

```typescript try_checkpointing.ts
import { query } from "@anthropic-ai/claude-agent-sdk";
import * as readline from "readline";

async function main() {
  // Configure the SDK with checkpointing enabled
  // - enableFileCheckpointing: Track file changes for rewinding
  // - permissionMode: Auto-accept file edits without prompting
  // - extraArgs: Required to receive user message UUIDs in the stream
  const opts = {
    enableFileCheckpointing: true,
    permissionMode: "acceptEdits" as const,
    extraArgs: { 'replay-user-messages': null }
  };

  let sessionId: string | undefined;    // Store the session ID for resuming
  let checkpointId: string | undefined; // Store the user message UUID for rewinding

  console.log("Running agent to add doc comments to utils.ts...\n");

  // Run the agent and capture checkpoint data from the response stream
  const response = query({
    prompt: "Add doc comments to utils.ts",
    options: opts
  });

  for await (const message of response) {
    // Capture the first user message UUID - this is our restore point
    if (message.type === "user" && message.uuid && !checkpointId) {
      checkpointId = message.uuid;
    }
    // Capture the session ID so we can resume later
    if ("session_id" in message) {
      sessionId = message.session_id;
    }
  }

  console.log("Done! Open utils.ts to see the added doc comments.\n");

  // Ask the user if they want to rewind the changes
  if (checkpointId && sessionId) {
    const rl = readline.createInterface({
      input: process.stdin,
      output: process.stdout
    });

    const answer = await new Promise<string>((resolve) => {
      rl.question("Rewind to remove the doc comments? (y/n): ", resolve);
    });
    rl.close();

    if (answer.toLowerCase() === "y") {
      // Resume the session with an empty prompt, then rewind
      const rewindQuery = query({
        prompt: "",  // Empty prompt opens the connection
        options: { ...opts, resume: sessionId }
      });

      for await (const msg of rewindQuery) {
        await rewindQuery.rewindFiles(checkpointId);  // Restore files
        break;
      }

      console.log("\n✓ File restored! Open utils.ts to verify the doc comments are gone.");
    } else {
      console.log("\nKept the modified file.");
    }
  }
}

main();
```

</CodeGroup>

This example demonstrates the complete checkpointing workflow:

1. **Enable checkpointing**: configure the SDK with `enable_file_checkpointing=True` and `permission_mode="acceptEdits"` to auto-approve file edits
2. **Capture checkpoint data**: as the agent runs, store the first user message UUID (your restore point) and the session ID
3. **Prompt for rewind**: after the agent finishes, check your utility file to see the doc comments, then decide if you want to undo the changes
4. **Resume and rewind**: if yes, resume the session with an empty prompt and call `rewind_files()` to restore the original file

</Step>

<Step title="Run the example">

Set the environment variable and run the script from the same directory as your utility file.

<Tip>
Open your utility file (`utils.py` or `utils.ts`) in your IDE or editor before running the script. You'll see the file update in real-time as the agent adds doc comments, then revert back to the original when you choose to rewind.
</Tip>

<Tabs>
  <Tab title="Python">
    ```bash
    export CLAUDE_CODE_ENABLE_SDK_FILE_CHECKPOINTING=1
    python try_checkpointing.py
    ```
  </Tab>
  <Tab title="TypeScript">
    ```bash
    export CLAUDE_CODE_ENABLE_SDK_FILE_CHECKPOINTING=1
    npx tsx try_checkpointing.ts
    ```
  </Tab>
</Tabs>

You'll see the agent add doc comments, then a prompt asking if you want to rewind. If you choose yes, the file is restored to its original state.

</Step>

</Steps>

## Limitations

File checkpointing has the following limitations:

| Limitation | Description |
|------------|-------------|
| Write/Edit/NotebookEdit tools only | Changes made through Bash commands are not tracked |
| Same session | Checkpoints are tied to the session that created them |
| File content only | Creating, moving, or deleting directories is not undone by rewinding |
| Local files | Remote or network files are not tracked |

## Troubleshooting

### Checkpointing options not recognized

If `enableFileCheckpointing` or `rewindFiles()` isn't available, you may be on an older SDK version.

**Solution**: Update to the latest SDK version:
- **Python**: `pip install --upgrade claude-agent-sdk`
- **TypeScript**: `npm install @anthropic-ai/claude-agent-sdk@latest`

### User messages don't have UUIDs

If `message.uuid` is `undefined` or missing, you're not receiving checkpoint UUIDs.

**Cause**: The `replay-user-messages` option isn't set.

**Solution**: Add `extra_args={"replay-user-messages": None}` (Python) or `extraArgs: { 'replay-user-messages': null }` (TypeScript) to your options.

### "No file checkpoint found for message" error

This error occurs when the checkpoint data doesn't exist for the specified user message UUID.

**Common causes**:
- The `CLAUDE_CODE_ENABLE_SDK_FILE_CHECKPOINTING` environment variable isn't set
- The session wasn't properly completed before attempting to resume and rewind

**Solution**: Make sure you've set the environment variable (see [Set the environment variable](#set-the-environment-variable)), then use the pattern shown in the examples: capture the first user message UUID, complete the session fully, then resume with an empty prompt and call `rewindFiles()` once.

### "ProcessTransport is not ready for writing" error

This error occurs when you call `rewindFiles()` or `rewind_files()` after you've finished iterating through the response. The connection to the CLI process closes when the loop completes.

**Solution**: Resume the session with an empty prompt, then call rewind on the new query:

<CodeGroup>

```python Python
# Resume session with empty prompt, then rewind
async with ClaudeSDKClient(ClaudeAgentOptions(
    enable_file_checkpointing=True,
    resume=session_id
)) as client:
    await client.query("")
    async for message in client.receive_response():
        await client.rewind_files(checkpoint_id)
        break
```

```typescript TypeScript
// Resume session with empty prompt, then rewind
const rewindQuery = query({
  prompt: "",
  options: { ...opts, resume: sessionId }
});

for await (const msg of rewindQuery) {
  await rewindQuery.rewindFiles(checkpointId);
  break;
}
```

</CodeGroup>

## Next steps

- **[Sessions](/docs/en/agent-sdk/sessions)**: learn how to resume sessions, which is required for rewinding after the stream completes. Covers session IDs, resuming conversations, and session forking.
- **[Permissions](/docs/en/agent-sdk/permissions)**: configure which tools Claude can use and how file modifications are approved. Useful if you want more control over when edits happen.
- **[TypeScript SDK reference](/docs/en/agent-sdk/typescript)**: complete API reference including all options for `query()` and the `rewindFiles()` method.
- **[Python SDK reference](/docs/en/agent-sdk/python)**: complete API reference including all options for `ClaudeAgentOptions` and the `rewind_files()` method.