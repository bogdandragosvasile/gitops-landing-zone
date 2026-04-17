---
name: keycloak-admin
description: Call the Keycloak Admin REST API. Use for realm, user, client, and role management.
allowed-tools: Bash(curl *) Read
---

# Keycloak Admin API Call

Call the Keycloak Admin API. Arguments: `<METHOD> <endpoint> [json-body]`

Example: `GET /admin/realms/employee/users`
Example: `POST /admin/realms/employee/users {"username":"test"}`

Steps:
1. Read credentials from `.env`: `KEYCLOAK_ADMIN_USER`, `KEYCLOAK_ADMIN_PASSWORD`
2. Get admin token:
   ```bash
   TOKEN=$(curl -sf -X POST "http://keycloak.local/realms/master/protocol/openid-connect/token" \
     -d "grant_type=password&client_id=admin-cli&username=$KEYCLOAK_ADMIN_USER&password=$KEYCLOAK_ADMIN_PASSWORD" \
     | grep -o '"access_token":"[^"]*"' | cut -d'"' -f4)
   ```
3. Execute: `curl -sf -X <METHOD> -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" [-d '<body>'] "http://keycloak.local<endpoint>"`
4. Parse and report the JSON response.

Base URL: `http://keycloak.local`
