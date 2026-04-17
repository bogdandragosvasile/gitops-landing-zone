package careerforge.k8s

import future.keywords.if
import future.keywords.in

# ── Deny wildcard verbs in Roles/ClusterRoles ─────────────────────────────── #
deny[msg] if {
    input.kind in {"Role", "ClusterRole"}
    rule := input.rules[_]
    "*" in rule.verbs
    resource := rule.resources[_]
    msg := sprintf(
        "%v '%v' grants wildcard verbs on resource '%v'",
        [input.kind, input.metadata.name, resource],
    )
}

# ── Deny wildcard resources in Roles/ClusterRoles ─────────────────────────── #
deny[msg] if {
    input.kind in {"Role", "ClusterRole"}
    rule := input.rules[_]
    "*" in rule.resources
    msg := sprintf(
        "%v '%v' grants access to wildcard resources",
        [input.kind, input.metadata.name],
    )
}

# ── Deny secrets/* access for non-admin ServiceAccounts ──────────────────── #
_secret_verbs := {"get", "list", "watch", "create", "update", "patch", "delete", "*"}

deny[msg] if {
    input.kind == "Role"
    not startswith(input.metadata.name, "careerforge-admin")
    rule := input.rules[_]
    "secrets" in rule.resources
    verb := rule.verbs[_]
    verb in _secret_verbs
    msg := sprintf(
        "Role '%v' in namespace '%v' must not grant secret access to non-admin accounts",
        [input.metadata.name, input.metadata.namespace],
    )
}

# ── Warn: ClusterRoleBindings in careerforge namespace ───────────────────── #
warn[msg] if {
    input.kind == "ClusterRoleBinding"
    subject := input.subjects[_]
    subject.namespace == "careerforge"
    msg := sprintf(
        "ClusterRoleBinding '%v' binds a careerforge service account to a cluster-scoped role — prefer namespace-scoped Roles",
        [input.metadata.name],
    )
}
