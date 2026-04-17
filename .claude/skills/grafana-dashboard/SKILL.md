---
name: grafana-dashboard
description: Upload or validate a Grafana dashboard JSON. Use when managing monitoring dashboards.
allowed-tools: Bash(curl *)
---

# Grafana Dashboard

Upload dashboard JSON file `$ARGUMENTS` to Grafana.

Steps:
1. Read the dashboard JSON file
2. Wrap in API envelope: `{"dashboard": <json>, "overwrite": true}`
3. Upload: `curl -sf -X POST -H "Content-Type: application/json" -u "admin:CHANGE_ME_from_env" -d @- "http://grafana.local/api/dashboards/db"`
4. Report success or error.

Grafana URL: `http://grafana.local`
Credentials: `admin` / `CHANGE_ME_from_env`
