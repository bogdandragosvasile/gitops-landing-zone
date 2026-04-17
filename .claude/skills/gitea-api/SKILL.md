---
name: gitea-api
description: Call the Gitea REST API. Use for repo operations, user management, org management.
allowed-tools: Bash(curl *) Read
---

# Gitea API Call

Call the Gitea API. Arguments: `<METHOD> <endpoint> [json-body]`

Example: `GET /api/v1/repos/platform/gitops-infra`
Example: `POST /api/v1/orgs/platform/repos {"name":"new-repo"}`

Steps:
1. Read credentials from `.env`: `GITEA_ADMIN_USER`, `GITEA_ADMIN_PASSWORD`
2. Execute: `curl -sf -X <METHOD> -u "$GITEA_ADMIN_USER:$GITEA_ADMIN_PASSWORD" -H "Content-Type: application/json" [-d '<body>'] "http://gitea.local:3000<endpoint>"`
3. Parse and report the JSON response.

Base URL: `http://gitea.local:3000`
Auth: Basic auth with admin credentials from `.env`
