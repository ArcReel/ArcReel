# Hosting the Agent SDK

Deploy and host Claude Agent SDK in production environments

---

The Claude Agent SDK differs from traditional stateless LLM APIs in that it maintains conversational state and executes commands in a persistent environment. This guide covers the architecture, hosting considerations, and best practices for deploying SDK-based agents in production.

<Info>
For security hardening beyond basic sandboxing—including network controls, credential management, and isolation options—see [Secure Deployment](/docs/en/agent-sdk/secure-deployment).
</Info>

## Hosting Requirements

### Container-Based Sandboxing

For security and isolation, the SDK should run inside a sandboxed container environment. This provides process isolation, resource limits, network control, and ephemeral filesystems.

The SDK also supports [programmatic sandbox configuration](/docs/en/agent-sdk/typescript#sandbox-settings) for command execution.

### System Requirements

Each SDK instance requires:

- **Runtime dependencies**
  - Python 3.10+ (for Python SDK) or Node.js 18+ (for TypeScript SDK)
  - Node.js (required by Claude Code CLI)
  - Claude Code CLI: `npm install -g @anthropic-ai/claude-code`

- **Resource allocation**
  - Recommended: 1GiB RAM, 5GiB of disk, and 1 CPU (vary this based on your task as needed)

- **Network access**
  - Outbound HTTPS to `api.anthropic.com`
  - Optional: Access to MCP servers or external tools

## Understanding the SDK Architecture

Unlike stateless API calls, the Claude Agent SDK operates as a **long-running process** that:
- **Executes commands** in a persistent shell environment
- **Manages file operations** within a working directory
- **Handles tool execution** with context from previous interactions

## Sandbox Provider Options

Several providers specialize in secure container environments for AI code execution:

- **[Modal Sandbox](https://modal.com/docs/guide/sandbox)** - [demo implementation](https://modal.com/docs/examples/claude-slack-gif-creator)
- **[Cloudflare Sandboxes](https://github.com/cloudflare/sandbox-sdk)**
- **[Daytona](https://www.daytona.io/)**
- **[E2B](https://e2b.dev/)**
- **[Fly Machines](https://fly.io/docs/machines/)**
- **[Vercel Sandbox](https://vercel.com/docs/functions/sandbox)**

For self-hosted options (Docker, gVisor, Firecracker) and detailed isolation configuration, see [Isolation Technologies](/docs/en/agent-sdk/secure-deployment#isolation-technologies). 

## Production Deployment Patterns

### Pattern 1: Ephemeral Sessions

Create a new container for each user task, then destroy it when complete. 

Best for one-off tasks, the user may still interact with the AI while the task is completing, but once completed the container is destroyed.

**Examples:**
- Bug Investigation & Fix: Debug and resolve a specific issue with relevant context
- Invoice Processing: Extract and structure data from receipts/invoices for accounting systems
- Translation Tasks: Translate documents or content batches between languages
- Image/Video Processing: Apply transformations, optimizations, or extract metadata from media files

### Pattern 2: Long-Running Sessions

Maintain persistent container instances for long running tasks. Often times running _multiple_ Claude Agent processes inside of the container based on demand.

Best for proactive agents that take action without the users input, agents that serve content or agents that process high amounts of messages.

**Examples:**
- Email Agent: Monitors incoming emails and autonomously triages, responds, or takes actions based on content
- Site Builder: Hosts custom websites per user with live editing capabilities served through container ports
- High-Frequency Chat Bots: Handles continuous message streams from platforms like Slack where rapid response times are critical

### Pattern 3: Hybrid Sessions

Ephemeral containers that are hydrated with history and state, possibly from a database or from the SDK's session resumption features.

Best for containers with intermittent interaction from the user that kicks off work and spins down when the work is completed but can be continued.

**Examples:**
- Personal Project Manager: Helps manage ongoing projects with intermittent check-ins, maintains context of tasks, decisions, and progress
- Deep Research: Conducts multi-hour research tasks, saves findings and resumes investigation when user returns
- Customer Support Agent: Handles support tickets that span multiple interactions, loads ticket history and customer context

### Pattern 4: Single Containers

Run multiple Claude Agent SDK processes in one global container.

Best for agents that must collaborate closely together. This is likely the least popular pattern because you will have to prevent agents from overwriting each other.

**Examples:**
- **Simulations**: Agents that interact with each other in simulations such as video games.

# FAQ

### How do I communicate with my sandboxes?
When hosting in containers, expose ports to communicate with your SDK instances. Your application can expose HTTP/WebSocket endpoints for external clients while the SDK runs internally within the container.

### What is the cost of hosting a container?
We have found that the dominant cost of serving agents is the tokens, containers vary based on what you provision but a minimum cost is roughly 5 cents per hour running.

### When should I shut down idle containers vs. keeping them warm?
This is likely provider dependent, different sandbox providers will let you set different criteria for idle timeouts after which a sandbox might spin down.
You will want to tune this timeout based on how frequent you think user response might be. 

### How often should I update the Claude Code CLI?
The Claude Code CLI is versioned with semver, so any breaking changes will be versioned.

### How do I monitor container health and agent performance?
Since containers are just servers the same logging infrastructure you use for the backend will work for containers.

### How long can an agent session run before timing out?
An agent session will not timeout, but we recommend setting a 'maxTurns' property to prevent Claude from getting stuck in a loop.

## Next Steps

- [Secure Deployment](/docs/en/agent-sdk/secure-deployment) - Network controls, credential management, and isolation hardening
- [TypeScript SDK - Sandbox Settings](/docs/en/agent-sdk/typescript#sandbox-settings) - Configure sandbox programmatically
- [Sessions Guide](/docs/en/agent-sdk/sessions) - Learn about session management
- [Permissions](/docs/en/agent-sdk/permissions) - Configure tool permissions
- [Cost Tracking](/docs/en/agent-sdk/cost-tracking) - Monitor API usage
- [MCP Integration](/docs/en/agent-sdk/mcp) - Extend with custom tools