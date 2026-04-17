package careerforge.k8s

import future.keywords.if
import future.keywords.in

# ── Allowed image registries ─────────────────────────────────────────────── #
# Only images from trusted registries may run in the careerforge namespace.
allowed_registries := {
    "gitea.local/careerforge/",
    "pgvector/",
    "redis:",
    "ollama/ollama:",
}

_image_allowed(image) if {
    registry := allowed_registries[_]
    startswith(image, registry)
}

deny[msg] if {
    input.kind in {"Deployment", "StatefulSet", "DaemonSet"}
    container := input.spec.template.spec.containers[_]
    not _image_allowed(container.image)
    msg := sprintf(
        "Container '%v' in '%v/%v' uses image '%v' from an untrusted registry",
        [container.name, input.kind, input.metadata.name, container.image],
    )
}

# ── Deny images with no tag (implicit :latest) ───────────────────────────── #
deny[msg] if {
    input.kind in {"Deployment", "StatefulSet", "DaemonSet"}
    container := input.spec.template.spec.containers[_]
    not contains(container.image, ":")
    msg := sprintf(
        "Container '%v' in '%v/%v' must specify an explicit image tag",
        [container.name, input.kind, input.metadata.name],
    )
}
