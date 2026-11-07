# ArcReel external Agent connection task

Help the user connect the current Agent host to ArcReel. Complete these steps before ending; installed skills and MCP tool descriptions provide subsequent creative-workflow guidance.

## 1. Install ArcReel skills

Run in this Agent’s working directory:

```bash
npx skills add ArcReel/skills
```

Verify both `setup-arcreel-skills` and `video-workflow` are installed.

## 2. Obtain connection information

Ask the user to open [Access tokens in ArcReel settings]({{BASE_URL}}/app/settings?section=access-tokens) and create an `arc-` prefixed token (API key). The full token appears only once; users may provide it to the current Agent they explicitly selected. `setup-arcreel-skills` persists the same connection through host/workspace local conventions for future sessions and skills.

MCP endpoint:

```text
{{BASE_URL}}/mcp
```

## 3. Connect

Immediately use `setup-arcreel-skills` after installation to configure the MCP endpoint, Bearer token, and connectivity. `video-workflow` triggers later for creative requests.

## 4. Completion

`list_projects` succeeds with structured `projects`, and host MCP/workspace configuration use the same connection. An empty list is successful.
