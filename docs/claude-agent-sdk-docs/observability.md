> ## Documentation Index
> Fetch the complete documentation index at: https://code.claude.com/docs/llms.txt
> Use this file to discover all available pages before exploring further.

# Observability with OpenTelemetry

> Export traces, metrics, and events from the Agent SDK to your observability backend using OpenTelemetry.

When you run agents in production, you need visibility into what they did:

* which tools they called
* how long each model request took
* how many tokens were spent
* where failures occurred

The Agent SDK can export this data as OpenTelemetry traces, metrics, and log events to any backend that accepts the OpenTelemetry Protocol (OTLP), such as Honeycomb, Datadog, Grafana, Langfuse, or a self-hosted collector.

This guide explains how the SDK emits telemetry, how to configure the export, and how to tag and filter the data once it reaches your backend. To read token usage and cost directly from the SDK response stream instead of exporting to a backend, see [Track cost and usage](/en/agent-sdk/cost-tracking).

## How telemetry flows from the SDK

The Agent SDK runs the Claude Code CLI as a child process and communicates with it over a local pipe. The CLI has OpenTelemetry instrumentation built in: it records spans around each model request and tool execution, emits metrics for token and cost counters, and emits structured log events for prompts and tool results. The SDK does not produce telemetry of its own. Instead, it passes configuration through to the CLI process, and the CLI exports directly to your collector.

Configuration is passed as environment variables. By default, the child process inherits your application's environment, so you can configure telemetry in either of two places:

* **Process environment:** set the variables in your shell, container, or orchestrator before your application starts. Every `query()` call picks them up automatically with no code change. This is the recommended approach for production deployments.
* **Per-call options:** set the variables in `ClaudeAgentOptions.env` (Python) or `options.env` (TypeScript). Use this when different agents in the same process need different telemetry settings. In Python, `env` is merged on top of the inherited environment. In TypeScript, `env` replaces the inherited environment entirely, so include `...process.env` in the object you pass.

The CLI exports three independent OpenTelemetry signals. Each has its own enable switch and its own exporter, so you can turn on only the ones you need.

| Signal     | What it contains                                                            | Enable with                                                         |
| ---------- | --------------------------------------------------------------------------- | ------------------------------------------------------------------- |
| Metrics    | Counters for tokens, cost, sessions, lines of code, and tool decisions      | `OTEL_METRICS_EXPORTER`                                             |
| Log events | Structured records for each prompt, API request, API error, and tool result | `OTEL_LOGS_EXPORTER`                                                |
| Traces     | Spans for each interaction, model request, tool call, and hook (beta)       | `OTEL_TRACES_EXPORTER` plus `CLAUDE_CODE_ENHANCED_TELEMETRY_BETA=1` |

For the complete list of metric names, event names, and attributes, see the Claude Code [Monitoring](/en/monitoring-usage) reference. The Agent SDK emits the same data because it runs the same CLI. Span names are listed in [Read agent traces](#read-agent-traces) below.

## Enable telemetry export

Telemetry is off until you set `CLAUDE_CODE_ENABLE_TELEMETRY=1` and choose at least one exporter. The most common configuration sends all three signals over OTLP HTTP to a collector.

The following example sets the variables in a dictionary and passes them through `options.env`. The agent runs a single task, and the CLI exports spans, metrics, and events to the collector at `collector.example.com` while the loop consumes the response stream:

<CodeGroup>
  ```python Python theme={null}
  import asyncio
  from claude_agent_sdk import query, ClaudeAgentOptions

  OTEL_ENV = {
      "CLAUDE_CODE_ENABLE_TELEMETRY": "1",
      # Required for traces, which are in beta. Metrics and log events do not need this.
      "CLAUDE_CODE_ENHANCED_TELEMETRY_BETA": "1",
      # Choose an exporter per signal. Use otlp for the SDK; see the Note below.
      "OTEL_TRACES_EXPORTER": "otlp",
      "OTEL_METRICS_EXPORTER": "otlp",
      "OTEL_LOGS_EXPORTER": "otlp",
      # Standard OTLP transport configuration.
      "OTEL_EXPORTER_OTLP_PROTOCOL": "http/protobuf",
      "OTEL_EXPORTER_OTLP_ENDPOINT": "http://collector.example.com:4318",
      "OTEL_EXPORTER_OTLP_HEADERS": "Authorization=Bearer your-token",
  }


  async def main():
      options = ClaudeAgentOptions(env=OTEL_ENV)
      async for message in query(
          prompt="List the files in this directory", options=options
      ):
          print(message)


  asyncio.run(main())
  ```

  ```typescript TypeScript theme={null}
  import { query } from "@anthropic-ai/claude-agent-sdk";

  const otelEnv = {
    CLAUDE_CODE_ENABLE_TELEMETRY: "1",
    // Required for traces, which are in beta. Metrics and log events do not need this.
    CLAUDE_CODE_ENHANCED_TELEMETRY_BETA: "1",
    // Choose an exporter per signal. Use otlp for the SDK; see the Note below.
    OTEL_TRACES_EXPORTER: "otlp",
    OTEL_METRICS_EXPORTER: "otlp",
    OTEL_LOGS_EXPORTER: "otlp",
    // Standard OTLP transport configuration.
    OTEL_EXPORTER_OTLP_PROTOCOL: "http/protobuf",
    OTEL_EXPORTER_OTLP_ENDPOINT: "http://collector.example.com:4318",
    OTEL_EXPORTER_OTLP_HEADERS: "Authorization=Bearer your-token",
  };

  for await (const message of query({
    prompt: "List the files in this directory",
    // env replaces the inherited environment in TypeScript, so spread
    // process.env first to keep PATH, ANTHROPIC_API_KEY, and other variables.
    options: { env: { ...process.env, ...otelEnv } },
  })) {
    console.log(message);
  }
  ```
</CodeGroup>

Because the child process inherits your application's environment by default, you can achieve the same result by exporting these variables in a Dockerfile, Kubernetes manifest, or shell profile and omitting `options.env` entirely.

<Note>
  The `console` exporter writes telemetry to standard output, which the SDK uses
  as its message channel. Do not set `console` as an exporter value when running
  through the SDK. To inspect telemetry locally, point
  `OTEL_EXPORTER_OTLP_ENDPOINT` at a local collector or an all-in-one Jaeger
  container instead.
</Note>

### Flush telemetry from short-lived calls

The CLI batches telemetry and exports on an interval. On a clean process exit it attempts to flush pending data, but the flush is bounded by a short timeout, so spans can still be dropped if the collector is slow to respond. If your process is killed before the CLI shuts down, anything still in the batch buffer is lost. Lowering the export intervals reduces both windows.

By default, metrics export every 60 seconds and traces and logs export every 5 seconds. The following example shortens all three intervals so that data reaches the collector while a short task is still running:

<CodeGroup>
  ```python Python theme={null}
  OTEL_ENV = {
      # ... exporter configuration from the previous example ...
      "OTEL_METRIC_EXPORT_INTERVAL": "1000",
      "OTEL_LOGS_EXPORT_INTERVAL": "1000",
      "OTEL_TRACES_EXPORT_INTERVAL": "1000",
  }
  ```

  ```typescript TypeScript theme={null}
  const otelEnv = {
    // ... exporter configuration from the previous example ...
    OTEL_METRIC_EXPORT_INTERVAL: "1000",
    OTEL_LOGS_EXPORT_INTERVAL: "1000",
    OTEL_TRACES_EXPORT_INTERVAL: "1000",
  };
  ```
</CodeGroup>

## Read agent traces

Traces give you the most detailed view of an agent run. With `CLAUDE_CODE_ENHANCED_TELEMETRY_BETA=1` set, each step of the agent loop becomes a span you can inspect in your tracing backend:

* **`claude_code.interaction`:** wraps a single turn of the agent loop, from receiving a prompt to producing a response.
* **`claude_code.llm_request`:** wraps each call to the Claude API, with model name, latency, and token counts as attributes.
* **`claude_code.tool`:** wraps each tool invocation, with child spans for the permission wait (`claude_code.tool.blocked_on_user`) and the execution itself (`claude_code.tool.execution`).
* **`claude_code.hook`:** wraps each [hook](/en/agent-sdk/hooks) execution. Requires detailed beta tracing (`ENABLE_BETA_TRACING_DETAILED=1` and `BETA_TRACING_ENDPOINT`) in addition to the variables above.

The `llm_request`, `tool`, and `hook` spans are children of the enclosing `claude_code.interaction` span. When the agent spawns a subagent through the Task tool, the subagent's `llm_request` and `tool` spans nest under the parent agent's `claude_code.tool` span, so the full delegation chain appears as one trace.

Spans carry a `session.id` attribute by default. When you make several `query()` calls against the same [session](/en/agent-sdk/sessions), filter on `session.id` in your backend to see them as one timeline. The attribute is omitted if `OTEL_METRICS_INCLUDE_SESSION_ID` is set to a falsy value.

<Note>
  Tracing is in beta. Span names and attributes may change between releases. See
  [Traces (beta)](/en/monitoring-usage#traces-beta) in the Monitoring reference
  for the trace exporter configuration variables.
</Note>

## Link traces to your application

The SDK automatically propagates W3C trace context into the CLI subprocess. When you call `query()` while an OpenTelemetry span is active in your application, the SDK injects `TRACEPARENT` and `TRACESTATE` into the child process environment, and the CLI reads them so its `claude_code.interaction` span becomes a child of your span. The agent run then appears inside your application's trace instead of as a disconnected root.

The CLI also forwards `TRACEPARENT` to every Bash and PowerShell command it runs. If a command launched through the Bash tool emits its own OpenTelemetry spans, those spans nest under the `claude_code.tool.execution` span that wraps the command.

Auto-injection is skipped when you set `TRACEPARENT` explicitly in `options.env`, so you can pin a specific parent context if needed. Interactive CLI sessions ignore inbound `TRACEPARENT` entirely; only Agent SDK and `claude -p` runs honor it. See [Traces (beta)](/en/monitoring-usage#traces-beta) in the Monitoring reference for the full span and attribute reference.

## Tag telemetry from your agent

By default, the CLI reports `service.name` as `claude-code`. If you run several agents, or run the SDK alongside other services that export to the same collector, override the service name and add resource attributes so you can filter by agent in your backend.

The following example renames the service and attaches deployment metadata. These values are applied as OpenTelemetry resource attributes on every span, metric, and event the agent emits:

<CodeGroup>
  ```python Python theme={null}
  options = ClaudeAgentOptions(
      env={
          # ... exporter configuration ...
          "OTEL_SERVICE_NAME": "support-triage-agent",
          "OTEL_RESOURCE_ATTRIBUTES": "service.version=1.4.0,deployment.environment=production",
      },
  )
  ```

  ```typescript TypeScript theme={null}
  const options = {
    env: {
      ...process.env,
      // ... exporter configuration ...
      OTEL_SERVICE_NAME: "support-triage-agent",
      OTEL_RESOURCE_ATTRIBUTES:
        "service.version=1.4.0,deployment.environment=production",
    },
  };
  ```
</CodeGroup>

## Attribute actions to your end users

The CLI attaches [identity attributes](/en/monitoring-usage#standard-attributes) to every event based on the credential it uses to call Anthropic. When you build an application that serves many end users from one deployment, these attributes identify your service's credential, not the end user on whose behalf the agent acted.

To make tool calls and MCP activity attributable to your application's end users, inject end-user identity as resource attributes on each `query()` call. Percent-encode values before interpolating them, since `OTEL_RESOURCE_ATTRIBUTES` [reserves commas, spaces, and equals signs](/en/monitoring-usage#multi-team-organization-support). The following example attaches the requesting user and tenant to every span and event from one request:

<CodeGroup>
  ```python Python theme={null}
  from urllib.parse import quote

  options = ClaudeAgentOptions(
      env={
          # ... exporter configuration ...
          "OTEL_RESOURCE_ATTRIBUTES": f"enduser.id={quote(request.user_id)},tenant.id={quote(request.tenant_id)}",
      },
  )
  ```

  ```typescript TypeScript theme={null}
  const options = {
    env: {
      ...process.env,
      // ... exporter configuration ...
      OTEL_RESOURCE_ATTRIBUTES: `enduser.id=${encodeURIComponent(request.userId)},tenant.id=${encodeURIComponent(request.tenantId)}`,
    },
  };
  ```
</CodeGroup>

With end-user identity attached, the `tool_decision`, `tool_result`, `mcp_server_connection`, and `permission_mode_changed` events become a per-user audit trail you can forward to a Security Information and Event Management (SIEM) platform. See [Audit security events](/en/monitoring-usage#audit-security-events) in the Monitoring reference for the full list of security-relevant events and the attributes each one carries.

## Control sensitive data in exports

Telemetry is structural by default. Durations, model names, and tool names are recorded on every span; token counts are recorded when the underlying API request returns usage data, so spans for failed or aborted requests may omit them. The content your agent reads and writes is not recorded by default. These opt-in variables add content to the exported data:

| Variable                  | Adds                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| ------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `OTEL_LOG_USER_PROMPTS=1` | Prompt text on `claude_code.user_prompt` events and on the `claude_code.interaction` span                                                                                                                                                                                                                                                                                                                                                                   |
| `OTEL_LOG_TOOL_DETAILS=1` | Tool input arguments (file paths, shell commands, search patterns) on `claude_code.tool_result` events                                                                                                                                                                                                                                                                                                                                                      |
| `OTEL_LOG_TOOL_CONTENT=1` | Full tool input and output bodies as span events on `claude_code.tool`, truncated at 60 KB. Requires [tracing](#read-agent-traces) to be enabled                                                                                                                                                                                                                                                                                                            |
| `OTEL_LOG_RAW_API_BODIES` | Full Anthropic Messages API request and response JSON as `claude_code.api_request_body` and `claude_code.api_response_body` log events. Set to `1` for inline bodies truncated at 60 KB, or `file:<dir>` for untruncated bodies on disk with a `body_ref` path in the event. Bodies include the entire conversation history and have extended-thinking content redacted. Enabling this implies consent to everything the three variables above would reveal |

Leave these unset unless your observability pipeline is approved to store the data your agent handles. See [Security and privacy](/en/monitoring-usage#security-and-privacy) in the Monitoring reference for the full list of attributes and redaction behavior.

## Related documentation

These guides cover adjacent topics for monitoring and deploying agents:

* [Track cost and usage](/en/agent-sdk/cost-tracking): read token and cost data from the message stream without an external backend.
* [Hosting the Agent SDK](/en/agent-sdk/hosting): deploy agents in containers where you can set OpenTelemetry variables at the environment level.
* [Monitoring](/en/monitoring-usage): the complete reference for every environment variable, metric, and event the CLI emits.
