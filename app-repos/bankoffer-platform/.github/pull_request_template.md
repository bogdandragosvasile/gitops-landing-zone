## Summary

<!-- Describe what this PR does and why. -->

## Changes

- 

## Agent Result

<!-- If this PR was created by an agent, include the structured result block below. -->

```yaml
agent-result:
  agent: ""            # e.g. infra_agent, api_agent, aiml_agent
  depth: 1             # 0 = orchestrator, 1 = domain agent, 2 = sub-agent
  parent: ""           # parent agent name (empty for depth 0/1)
  status: completed    # completed | blocked
  tasks_completed:
    - task_name: ""
      description: ""
      files_changed: []
  tasks_blocked: []    # list any tasks that could not be completed
  follow_up:
    - ""               # any follow-up actions needed from other agents
  audit_yaml_updated: true   # must be true before merging
  tests_passed: true         # must be true before merging
  security_scan: passed      # passed | failed | skipped
```

## Test Plan

- [ ] Unit tests pass (`pytest tests/unit/`)
- [ ] Integration tests pass (`pytest tests/integration/`)
- [ ] Linting passes (`ruff check .`)
- [ ] Docker image builds successfully
- [ ] Helm chart lints (`helm lint infra/helm/<chart>`)
- [ ] No secrets detected (`gitleaks detect`)

## Checklist

- [ ] `audit.yaml` history updated with completed tasks
- [ ] OpenAPI spec updated (if API changes)
- [ ] Documentation updated (if applicable)
- [ ] No hardcoded secrets or credentials
- [ ] Changes scoped to this agent's designated directories
