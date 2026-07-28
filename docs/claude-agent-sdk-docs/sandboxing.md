> ## Documentation Index
> Fetch the complete documentation index at: https://code.claude.com/docs/llms.txt
> Use this file to discover all available pages before exploring further.

# Sandboxing

> Learn how Claude Code's sandboxed bash tool provides filesystem and network isolation for safer, more autonomous agent execution.

## Overview

Claude Code features native sandboxing to provide a more secure environment for agent execution while reducing the need for constant permission prompts. Instead of asking permission for each bash command, sandboxing creates defined boundaries upfront where Claude Code can work more freely with reduced risk.

The sandboxed bash tool uses OS-level primitives to enforce both filesystem and network isolation.

## Why sandboxing matters

Traditional permission-based security requires constant user approval for bash commands. While this provides control, it can lead to:

* **Approval fatigue**: Repeatedly clicking "approve" can cause users to pay less attention to what they're approving
* **Reduced productivity**: Constant interruptions slow down development workflows
* **Limited autonomy**: Claude Code cannot work as efficiently when waiting for approvals

Sandboxing addresses these challenges by:

1. **Defining clear boundaries**: Specify exactly which directories and network hosts Claude Code can access
2. **Reducing permission prompts**: Safe commands within the sandbox don't require approval
3. **Maintaining security**: Attempts to access resources outside the sandbox trigger immediate notifications
4. **Enabling autonomy**: Claude Code can run more independently within defined limits

<Warning>
  Effective sandboxing requires **both** filesystem and network isolation. Without network isolation, a compromised agent could exfiltrate sensitive files like SSH keys. Without filesystem isolation, a compromised agent could backdoor system resources to gain network access. When configuring sandboxing it is important to ensure that your configured settings do not create bypasses in these systems.
</Warning>

## How it works

### Filesystem isolation

The sandboxed bash tool restricts file system access to specific directories:

* **Default writes behavior**: Read and write access to the current working directory and its subdirectories
* **Default read behavior**: Read access to the entire computer, except certain denied directories
* **Blocked access**: Cannot modify files outside the current working directory without explicit permission
* **Configurable**: Define custom allowed and denied paths through settings

You can grant write access to additional paths using `sandbox.filesystem.allowWrite` in your settings. These restrictions are enforced at the OS level (Seatbelt on macOS, bubblewrap on Linux), so they apply to all subprocess commands, including tools like `kubectl`, `terraform`, and `npm`, not just Claude's file tools.

### Network isolation

Network access is controlled through a proxy server running outside the sandbox:

* **Domain restrictions**: Only approved domains can be accessed
* **User confirmation**: New domain requests trigger permission prompts (unless [`allowManagedDomainsOnly`](/en/settings#sandbox-settings) is enabled, which blocks non-allowed domains automatically)
* **Custom proxy support**: Advanced users can implement custom rules on outgoing traffic
* **Comprehensive coverage**: Restrictions apply to all scripts, programs, and subprocesses spawned by commands

<Note>
  The built-in proxy enforces the allowlist based on the requested hostname and does not terminate or inspect TLS traffic. See [Security limitations](#security-limitations) for the implications of this design, and [Custom proxy configuration](#custom-proxy-configuration) if your threat model requires TLS inspection.
</Note>

### OS-level enforcement

The sandboxed bash tool leverages operating system security primitives:

* **macOS**: Uses Seatbelt for sandbox enforcement
* **Linux**: Uses [bubblewrap](https://github.com/containers/bubblewrap) for isolation
* **WSL2**: Uses bubblewrap, same as Linux

WSL1 is not supported because bubblewrap requires kernel features only available in WSL2.

These OS-level restrictions ensure that all child processes spawned by Claude Code's commands inherit the same security boundaries.

## Getting started

### Prerequisites

On **macOS**, sandboxing works out of the box using the built-in Seatbelt framework.

On **Linux and WSL2**, install the required packages first:

<Tabs>
  <Tab title="Ubuntu/Debian">
    ```bash theme={null}
    sudo apt-get install bubblewrap socat
    ```
  </Tab>

  <Tab title="Fedora">
    ```bash theme={null}
    sudo dnf install bubblewrap socat
    ```
  </Tab>
</Tabs>

On Ubuntu 24.04 and later, the default AppArmor policy prevents bubblewrap from creating the user namespaces it needs for isolation. Add an AppArmor profile that grants `bwrap` this capability:

```bash theme={null}
sudo tee /etc/apparmor.d/bwrap > /dev/null <<'EOF'
abi <abi/4.0>,
include <tunables/global>

profile bwrap /usr/bin/bwrap flags=(unconfined) {
  userns,
  include if exists <local/bwrap>
}
EOF
```

The profile applies only to `bwrap` itself, not to the commands it runs inside the sandbox. Reload AppArmor to apply it:

```bash theme={null}
sudo systemctl reload apparmor
```

WSL1 does not support sandboxing because it lacks the required Linux namespace primitives. If you see `Sandboxing requires WSL2`, upgrade your distribution to WSL2 or run Claude Code without sandboxing.

On WSL2, sandboxed commands cannot launch Windows binaries such as `cmd.exe`, `powershell.exe`, or anything under `/mnt/c/`. WSL hands these off to the Windows host over a Unix socket, which the sandbox blocks. If a command needs to invoke a Windows binary, add it to [`excludedCommands`](/en/settings#sandbox-settings) so it runs outside the sandbox.

### Enable sandboxing

You can enable sandboxing by running the `/sandbox` command:

```text theme={null}
/sandbox
```

This opens a menu where you can choose between sandbox modes. If required dependencies are missing (such as `bubblewrap` or `socat` on Linux), the menu displays installation instructions for your platform.

By default, if the sandbox cannot start (missing dependencies or unsupported platform), Claude Code shows a warning and runs commands without sandboxing. To make this a hard failure instead, set [`sandbox.failIfUnavailable`](/en/settings#sandbox-settings) to `true`. This is intended for managed deployments that require sandboxing as a security gate.

### Sandbox modes

Claude Code offers two sandbox modes:

**Auto-allow mode**: Bash commands will attempt to run inside the sandbox and are automatically allowed without requiring permission. Commands that cannot be sandboxed (such as those needing network access to non-allowed hosts) fall back to the regular permission flow. Explicit deny rules are always respected, and `rm` or `rmdir` commands that target `/`, your home directory, or other critical system paths still trigger a permission prompt. Ask rules apply only to commands that fall back to the regular permission flow.

**Regular permissions mode**: All bash commands go through the standard permission flow, even when sandboxed. This provides more control but requires more approvals.

In both modes, the sandbox enforces the same filesystem and network restrictions. The difference is only in whether sandboxed commands are auto-approved or require explicit permission.

<Info>
  Auto-allow mode works independently of your permission mode setting. Even if you're not in "accept edits" mode, sandboxed bash commands will run automatically when auto-allow is enabled. This means bash commands that modify files within the sandbox boundaries will execute without prompting, even when file edit tools would normally require approval.
</Info>

### Configure sandboxing

Customize sandbox behavior through your `settings.json` file. See [Settings](/en/settings#sandbox-settings) for complete configuration reference.

#### Granting subprocess write access to specific paths

By default, sandboxed commands can only write to the current working directory. If subprocess commands like `kubectl`, `terraform`, or `npm` need to write outside the project directory, use `sandbox.filesystem.allowWrite` to grant access to specific paths:

```json theme={null}
{
  "sandbox": {
    "enabled": true,
    "filesystem": {
      "allowWrite": ["~/.kube", "/tmp/build"]
    }
  }
}
```

These paths are enforced at the OS level, so all commands running inside the sandbox, including their child processes, respect them. This is the recommended approach when a tool needs write access to a specific location, rather than excluding the tool from the sandbox entirely with `excludedCommands`.

When `allowWrite` (or `denyWrite`/`denyRead`/`allowRead`) is defined in multiple [settings scopes](/en/settings#settings-precedence), the arrays are **merged**, meaning paths from every scope are combined, not replaced. For example, if managed settings allow writes to `/opt/company-tools` and a user adds `~/.kube` in their personal settings, both paths are included in the final sandbox configuration. This means users and projects can extend the list without duplicating or overriding paths set by higher-priority scopes.

Path prefixes control how paths are resolved:

| Prefix            | Meaning                                                                                | Example                                                                   |
| :---------------- | :------------------------------------------------------------------------------------- | :------------------------------------------------------------------------ |
| `/`               | Absolute path from filesystem root                                                     | `/tmp/build` stays `/tmp/build`                                           |
| `~/`              | Relative to home directory                                                             | `~/.kube` becomes `$HOME/.kube`                                           |
| `./` or no prefix | Relative to the project root for project settings, or to `~/.claude` for user settings | `./output` in `.claude/settings.json` resolves to `<project-root>/output` |

The older `//path` prefix for absolute paths still works. If you previously used single-slash `/path` expecting project-relative resolution, switch to `./path`. This syntax differs from [Read and Edit permission rules](/en/permissions#read-and-edit), which use `//path` for absolute and `/path` for project-relative. Sandbox filesystem paths use standard conventions: `/tmp/build` is an absolute path.

You can also deny write or read access using `sandbox.filesystem.denyWrite` and `sandbox.filesystem.denyRead`. These are merged with any paths from `Edit(...)` and `Read(...)` permission rules. To re-allow reading specific paths within a denied region, use `sandbox.filesystem.allowRead`, which takes precedence over `denyRead`. When `allowManagedReadPathsOnly` is enabled in managed settings, only managed `allowRead` entries are respected; user, project, and local `allowRead` entries are ignored. `denyRead` still merges from all sources.

For example, to block reading from the entire home directory while still allowing reads from the current project, add this to your project's `.claude/settings.json`:

```json theme={null}
{
  "sandbox": {
    "enabled": true,
    "filesystem": {
      "denyRead": ["~/"],
      "allowRead": ["."]
    }
  }
}
```

The `.` in `allowRead` resolves to the project root because this configuration lives in project settings. If you placed the same configuration in `~/.claude/settings.json`, `.` would resolve to `~/.claude` instead, and project files would remain blocked by the `denyRead` rule.

<Tip>
  Not all commands are compatible with sandboxing out of the box. Some notes that may help you make the most out of the sandbox:

  * Many CLI tools require accessing certain hosts. As you use these tools, they will request permission to access certain hosts. Granting permission will allow them to access these hosts now and in the future, enabling them to safely execute inside the sandbox.
  * `watchman` is incompatible with running in the sandbox. If you're running `jest`, consider using `jest --no-watchman`
  * `docker` is incompatible with running in the sandbox. Consider specifying `docker *` in `excludedCommands` to force it to run outside of the sandbox.
</Tip>

<Note>
  Claude Code includes an intentional escape hatch mechanism that allows commands to run outside the sandbox when necessary. When a command fails due to sandbox restrictions (such as network connectivity issues or incompatible tools), Claude is prompted to analyze the failure and may retry the command with the `dangerouslyDisableSandbox` parameter. Commands that use this parameter go through the normal Claude Code permissions flow requiring user permission to execute. This allows Claude Code to handle edge cases where certain tools or network operations cannot function within sandbox constraints.

  You can disable this escape hatch by setting `"allowUnsandboxedCommands": false` in your [sandbox settings](/en/settings#sandbox-settings). When disabled, the `dangerouslyDisableSandbox` parameter is completely ignored and all commands must run sandboxed or be explicitly listed in `excludedCommands`.
</Note>

## Security benefits

### Protection against prompt injection

Even if an attacker successfully manipulates Claude Code's behavior through prompt injection, the sandbox ensures your system remains secure:

**Filesystem protection:**

* Cannot modify critical config files such as `~/.bashrc`
* Cannot modify system-level files in `/bin/`
* Cannot read files that are denied in your [Claude permission settings](/en/permissions#manage-permissions)

**Network protection:**

* Cannot exfiltrate data to attacker-controlled servers
* Cannot download malicious scripts from unauthorized domains
* Cannot make unexpected API calls to unapproved services
* Cannot contact any domains not explicitly allowed

**Monitoring and control:**

* All access attempts outside the sandbox are blocked at the OS level
* You receive immediate notifications when boundaries are tested
* You can choose to deny, allow once, or permanently update your configuration

### Reduced attack surface

Sandboxing limits the potential damage from:

* **Malicious dependencies**: NPM packages or other dependencies with harmful code
* **Compromised scripts**: Build scripts or tools with security vulnerabilities
* **Social engineering**: Attacks that trick users into running dangerous commands
* **Prompt injection**: Attacks that trick Claude into running dangerous commands

### Transparent operation

When Claude Code attempts to access network resources outside the sandbox:

1. The operation is blocked at the OS level
2. You receive an immediate notification
3. You can choose to:
   * Deny the request
   * Allow it once
   * Update your sandbox configuration to permanently allow it

## Security Limitations

* Network Sandboxing Limitations: The network filtering system operates by restricting the domains that processes are allowed to connect to. The built-in proxy does not terminate or perform TLS inspection on outbound traffic, so the contents of encrypted connections are not examined. You are responsible for ensuring that only trusted domains are allowed in your policy.

<Warning>
  Allowing broad domains such as `github.com` can create paths for data exfiltration. Because the proxy makes its allow decision from the client-supplied hostname without inspecting TLS, code running inside the sandbox can potentially use [domain fronting](https://en.wikipedia.org/wiki/Domain_fronting) or similar techniques to reach hosts outside the allowlist. If your threat model requires stronger guarantees, configure a [custom proxy](#custom-proxy-configuration) that terminates TLS and inspects traffic, and install its CA certificate inside the sandbox. Stronger TLS-aware network isolation is an active area of development.
</Warning>

* Privilege Escalation via Unix Sockets: The `allowUnixSockets` configuration can inadvertently grant access to powerful system services that could lead to sandbox bypasses. For example, if it is used to allow access to `/var/run/docker.sock` this would effectively grant access to the host system through exploiting the docker socket. Users are encouraged to carefully consider any unix sockets that they allow through the sandbox.
* Filesystem Permission Escalation: Overly broad filesystem write permissions can enable privilege escalation attacks. Allowing writes to directories containing executables in `$PATH`, system configuration directories, or user shell configuration files (`.bashrc`, `.zshrc`) can lead to code execution in different security contexts when other users or system processes access these files.
* Linux Sandbox Strength: The Linux implementation provides strong filesystem and network isolation but includes an `enableWeakerNestedSandbox` mode that enables it to work inside of Docker environments without privileged namespaces. This option considerably weakens security and should only be used in cases where additional isolation is otherwise enforced.

## How sandboxing relates to permissions

Sandboxing and [permissions](/en/permissions) are complementary security layers that work together:

* **Permissions** control which tools Claude Code can use and are evaluated before any tool runs. They apply to all tools: Bash, Read, Edit, WebFetch, MCP, and others.
* **Sandboxing** provides OS-level enforcement that restricts what Bash commands can access at the filesystem and network level. It applies only to Bash commands and their child processes.

Filesystem and network restrictions are configured through both sandbox settings and permission rules:

* Use `sandbox.filesystem.allowWrite` to grant subprocess write access to paths outside the working directory
* Use `sandbox.filesystem.denyWrite` and `sandbox.filesystem.denyRead` to block subprocess access to specific paths
* Use `sandbox.filesystem.allowRead` to re-allow reading specific paths within a `denyRead` region
* Use `Read` and `Edit` deny rules to block access to specific files or directories
* Use `WebFetch` allow/deny rules to control domain access
* Use sandbox `allowedDomains` to control which domains Bash commands can reach
* Use sandbox `deniedDomains` to block specific domains even when a broader `allowedDomains` wildcard would otherwise permit them

Paths from both `sandbox.filesystem` settings and permission rules are merged together into the final sandbox configuration.

This [repository](https://github.com/anthropics/claude-code/tree/main/examples/settings) includes starter settings configurations for common deployment scenarios, including sandbox-specific examples. Use these as starting points and adjust them to fit your needs.

## Advanced usage

### Custom proxy configuration

For organizations requiring advanced network security, you can implement a custom proxy to:

* Decrypt and inspect HTTPS traffic
* Apply custom filtering rules
* Log all network requests
* Integrate with existing security infrastructure

```json theme={null}
{
  "sandbox": {
    "network": {
      "httpProxyPort": 8080,
      "socksProxyPort": 8081
    }
  }
}
```

### Integration with existing security tools

The sandboxed bash tool works alongside:

* **Permission rules**: Combine with [permission settings](/en/permissions) for defense-in-depth
* **Development containers**: Use with [dev containers](/en/devcontainer) for additional isolation
* **Enterprise policies**: Enforce sandbox configurations through [managed settings](/en/settings#settings-precedence)

## Best practices

1. **Start restrictive**: Begin with minimal permissions and expand as needed
2. **Monitor logs**: Review sandbox violation attempts to understand Claude Code's needs
3. **Use environment-specific configs**: Different sandbox rules for development vs. production contexts
4. **Combine with permissions**: Use sandboxing alongside IAM policies for comprehensive security
5. **Test configurations**: Verify your sandbox settings don't block legitimate workflows

## Open source

The sandbox runtime is available as an open source npm package for use in your own agent projects. This enables the broader AI agent community to build safer, more secure autonomous systems. This can also be used to sandbox other programs you may wish to run. For example, to sandbox an MCP server you could run:

```bash theme={null}
npx @anthropic-ai/sandbox-runtime <command-to-sandbox>
```

For implementation details and source code, visit the [GitHub repository](https://github.com/anthropic-experimental/sandbox-runtime).

## Limitations

* **Performance overhead**: Minimal, but some filesystem operations may be slightly slower
* **Compatibility**: Some tools that require specific system access patterns may need configuration adjustments, or may even need to be run outside of the sandbox
* **Platform support**: Supports macOS, Linux, and WSL2. WSL1 is not supported. Native Windows support is planned.

## What sandboxing does not cover

The sandbox isolates Bash subprocesses. Other tools operate under different boundaries:

* **Built-in file tools**: Read, Edit, and Write use the permission system directly rather than running through the sandbox. See [permissions](/en/permissions).
* **Computer use**: when Claude opens apps and controls your screen, it runs on your actual desktop rather than in an isolated environment. Per-app permission prompts gate each application. See [computer use in the CLI](/en/computer-use) or [computer use in Desktop](/en/desktop#let-claude-use-your-computer).

## See also

* [Security](/en/security) - Comprehensive security features and best practices
* [Permissions](/en/permissions) - Permission configuration and access control
* [Settings](/en/settings) - Complete configuration reference
* [CLI reference](/en/cli-reference) - Command-line options
