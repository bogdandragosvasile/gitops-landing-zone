# Agent Communication Protocol

## 1. Overview

BankOffer AI is built and maintained by a hierarchy of Claude Code agents. These agents do not share memory or communicate through a message bus. Instead, all coordination happens through Git artifacts: a central audit file, pull requests, GitHub Issues, and repository_dispatch events.

This document specifies the exact protocol every agent must follow.

## 2. Communication Channels

### 2.1 audit.yaml (Primary State)

The file `audit.yaml` at the repository root is the single source of truth for all agent state. It is an append-only log with two sections:

- **agents** -- A map of every agent, its status, depth, task list, and parent relationship.
- **history** -- An ordered list of events recording every state change.

Rules:

- Every agent MUST read `audit.yaml` before starting any work.
- Every agent MUST update its own status block after completing or failing a task.
- Every agent MUST append an entry to the `history` array for each state change.
- History entries are NEVER deleted. The audit policy is strictly append-only.

Example history entry:

```yaml
history:
  - timestamp: "2026-03-31T12:00:00Z"
    agent: infra_agent
    action: task_completed
    task: provision_k8s_namespaces
    details: "Created dev, staging, production namespaces in EKS cluster"
    pr: null
    issue: null
```

### 2.2 Pull Requests

Each agent opens a pull request when its task batch is complete. The PR is the primary artifact for code review and integration.

**PR naming convention:**

```
[agent_name] Brief description of changes
```

Examples:
- `[infra_agent] Add Terraform modules and Helm charts`
- `[security_sub] Add OPA policies and Trivy scan workflow`

**PR body requirements:**

Every agent-created PR MUST include the `agent-result` YAML block in its body. This block is defined in `.github/pull_request_template.md` and contains:

- Agent name and depth
- List of completed tasks with changed files
- List of blocked tasks (if any)
- Follow-up actions for other agents
- Confirmation that `audit.yaml` was updated
- Test and security scan status

**PR merge rules:**

- PRs from depth-2 sub-agents are reviewed by their parent agent.
- PRs from depth-1 domain agents are reviewed by the orchestrator.
- PRs from the orchestrator require human approval.
- All PRs must pass CI checks before merging.

### 2.3 GitHub Issues

Agents create GitHub Issues to communicate blockers, request human decisions, or propose architectural changes.

**Issue labeling:**

| Label              | Purpose                                              |
|--------------------|------------------------------------------------------|
| `agent:blocked`    | Agent cannot proceed without resolution              |
| `agent:question`   | Agent needs a human decision                         |
| `agent:proposal`   | Agent proposes an architectural change                |
| `agent:handoff`    | Agent is handing a task to another agent              |
| `priority:high`    | Blocks multiple agents or critical path              |
| `priority:medium`  | Blocks one agent, has workaround                     |
| `priority:low`     | Informational, no immediate action needed            |

**Issue body format:**

```markdown
## Agent
Name: infra_agent
Depth: 1

## Problem
<description of the blocker or question>

## Impact
<which agents/tasks are affected>

## Proposed Resolution
<what the agent thinks should happen>

## Context
- audit.yaml status: blocked
- Related PR: #<number> (if applicable)
- Related tasks: <task names>
```

### 2.4 repository_dispatch Events

The orchestrator agent uses GitHub's `repository_dispatch` API to trigger child agents after updating `audit.yaml`.

**Event types:**

| Event Type                      | Triggered By   | Wakes          |
|---------------------------------|----------------|----------------|
| `dispatch_infra_agent`          | orchestrator   | infra_agent    |
| `dispatch_data_agent`           | orchestrator   | data_pipeline  |
| `dispatch_aiml_agent`           | orchestrator   | aiml_agent     |
| `dispatch_api_agent`            | orchestrator   | api_agent      |
| `dispatch_notification_agent`   | orchestrator   | notification   |

Sub-agents (depth 2) are dispatched by their parent agents using the same mechanism:

| Event Type                      | Triggered By   | Wakes             |
|---------------------------------|----------------|-------------------|
| `dispatch_gitops_sub`           | infra_agent    | gitops_sub        |
| `dispatch_security_sub`         | api_agent      | security_sub      |
| `dispatch_observability_sub`    | infra_agent    | observability_sub |
| `dispatch_test_qa_sub`          | api_agent      | test_qa_sub       |

**Dispatch payload:**

```json
{
  "event_type": "dispatch_infra_agent",
  "client_payload": {
    "triggered_by": "orchestrator",
    "timestamp": "2026-03-31T12:00:00Z",
    "tasks": [
      "provision_k8s_namespaces",
      "write_terraform_modules",
      "write_helm_charts",
      "configure_argocd_app_of_apps",
      "write_github_actions_infra_workflow",
      "open_pr_infra"
    ]
  }
}
```

## 3. Agent Lifecycle

### 3.1 Status Transitions

```
pending ----[dispatched]----> active
active  ----[all tasks done]-> completed
active  ----[task failed]----> blocked
blocked ----[issue resolved]--> active
```

### 3.2 Startup Sequence

When an agent is activated (either by a repository_dispatch event or manual trigger):

1. Clone the repository (or pull latest).
2. Read `audit.yaml` and verify own status is `active` or has been set to `active` by the dispatcher.
3. Read the task list and identify the next incomplete task.
4. Execute the task.
5. Update `audit.yaml`: mark the task as completed, append to history.
6. Commit and push the `audit.yaml` update.
7. Repeat steps 3-6 until all tasks are done.
8. Open a pull request with all changes.
9. Set own status to `completed` in `audit.yaml`.
10. If sub-agents exist, dispatch them via repository_dispatch.

### 3.3 Failure Handling

If a task fails:

1. Set own status to `blocked` in `audit.yaml`.
2. Append an error entry to the history array with the failure details.
3. Open a GitHub Issue with the `agent:blocked` label.
4. Stop processing further tasks until the issue is resolved.

### 3.4 Conflict Resolution

If two agents attempt to modify `audit.yaml` simultaneously:

1. The second agent will encounter a merge conflict on push.
2. The agent must pull, rebase its changes onto the latest `audit.yaml`, and retry.
3. Only the `history` array and the agent's own status block should be modified, so conflicts should be rare and mechanically resolvable.

## 4. Directory Ownership

Each agent is responsible for specific directories. Agents must not modify files outside their owned directories unless the change is part of a coordinated PR that has been reviewed by the owning agent.

| Agent                | Owned Directories                                |
|----------------------|--------------------------------------------------|
| orchestrator         | `audit.yaml`, `CLAUDE.md`, root configs          |
| infra_agent          | `infra/`                                         |
| data_pipeline_agent  | `data/`                                          |
| aiml_agent           | `ml/`                                            |
| api_agent            | `services/api/`, `services/worker/`              |
| notification_agent   | `services/notification/`                         |
| gitops_sub           | `gitops/`                                        |
| security_sub         | `security/`                                      |
| observability_sub    | `observability/`                                 |
| test_qa_sub          | `tests/`                                         |

## 5. Orchestrator Responsibilities

The orchestrator (depth 0) is the only agent that:

- Modifies the top-level `agents` structure in `audit.yaml` (other agents only modify their own block).
- Dispatches depth-1 agents.
- Merges PRs from depth-1 agents (or requests human review).
- Decides task ordering and parallelism across domain agents.
- Handles escalations from blocked agents when no parent agent exists.

## 6. Human Intervention Points

Certain actions require human approval:

- Merging the orchestrator's own PRs.
- Resolving `priority:high` issues that block multiple agents.
- Approving production deployments (via ArgoCD manual sync).
- Modifying the `meta` section of `audit.yaml`.
- Adding new agents to the hierarchy.
