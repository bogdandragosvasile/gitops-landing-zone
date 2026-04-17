package careerforge.k8s

import future.keywords.if
import future.keywords.in

# ── Deny containers running as root ──────────────────────────────────────── #
deny[msg] if {
    input.kind in {"Deployment", "StatefulSet", "DaemonSet"}
    container := input.spec.template.spec.containers[_]
    not container.securityContext.runAsNonRoot
    msg := sprintf(
        "Container '%v' in '%v/%v' must set securityContext.runAsNonRoot=true",
        [container.name, input.kind, input.metadata.name],
    )
}

deny[msg] if {
    input.kind in {"Deployment", "StatefulSet", "DaemonSet"}
    container := input.spec.template.spec.containers[_]
    container.securityContext.runAsUser == 0
    msg := sprintf(
        "Container '%v' in '%v/%v' must not run as UID 0",
        [container.name, input.kind, input.metadata.name],
    )
}

# ── Deny privileged containers ───────────────────────────────────────────── #
deny[msg] if {
    input.kind in {"Deployment", "StatefulSet", "DaemonSet"}
    container := input.spec.template.spec.containers[_]
    container.securityContext.privileged == true
    msg := sprintf(
        "Container '%v' in '%v/%v' must not be privileged",
        [container.name, input.kind, input.metadata.name],
    )
}

# ── Require resource limits ──────────────────────────────────────────────── #
deny[msg] if {
    input.kind in {"Deployment", "StatefulSet", "DaemonSet"}
    container := input.spec.template.spec.containers[_]
    not container.resources.limits.memory
    msg := sprintf(
        "Container '%v' in '%v/%v' must set resources.limits.memory",
        [container.name, input.kind, input.metadata.name],
    )
}

deny[msg] if {
    input.kind in {"Deployment", "StatefulSet", "DaemonSet"}
    container := input.spec.template.spec.containers[_]
    not container.resources.limits.cpu
    msg := sprintf(
        "Container '%v' in '%v/%v' must set resources.limits.cpu",
        [container.name, input.kind, input.metadata.name],
    )
}

# ── Require liveness + readiness probes ─────────────────────────────────── #
deny[msg] if {
    input.kind in {"Deployment", "StatefulSet"}
    container := input.spec.template.spec.containers[_]
    not container.livenessProbe
    msg := sprintf(
        "Container '%v' in '%v/%v' must define a livenessProbe",
        [container.name, input.kind, input.metadata.name],
    )
}

deny[msg] if {
    input.kind in {"Deployment", "StatefulSet"}
    container := input.spec.template.spec.containers[_]
    not container.readinessProbe
    msg := sprintf(
        "Container '%v' in '%v/%v' must define a readinessProbe",
        [container.name, input.kind, input.metadata.name],
    )
}

# ── Deny :latest image tag ───────────────────────────────────────────────── #
deny[msg] if {
    input.kind in {"Deployment", "StatefulSet", "DaemonSet"}
    container := input.spec.template.spec.containers[_]
    endswith(container.image, ":latest")
    msg := sprintf(
        "Container '%v' in '%v/%v' must not use ':latest' image tag",
        [container.name, input.kind, input.metadata.name],
    )
}

# ── Require app label ────────────────────────────────────────────────────── #
deny[msg] if {
    input.kind in {"Deployment", "StatefulSet", "DaemonSet"}
    not input.metadata.labels.app
    msg := sprintf(
        "%v '%v' must have an 'app' label",
        [input.kind, input.metadata.name],
    )
}

# ── Deny hostNetwork ────────────────────────────────────────────────────── #
deny[msg] if {
    input.kind in {"Deployment", "StatefulSet", "DaemonSet", "Pod"}
    input.spec.template.spec.hostNetwork == true
    msg := sprintf(
        "%v '%v' must not use hostNetwork",
        [input.kind, input.metadata.name],
    )
}

# ── Warn: no ServiceAccount specified ────────────────────────────────────── #
warn[msg] if {
    input.kind in {"Deployment", "StatefulSet"}
    not input.spec.template.spec.serviceAccountName
    msg := sprintf(
        "%v '%v' should specify a serviceAccountName",
        [input.kind, input.metadata.name],
    )
}
