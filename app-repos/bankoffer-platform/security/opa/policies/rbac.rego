package bankoffer.rbac

import future.keywords.if
import future.keywords.in

# Role definitions
roles := {
  "admin": {
    "permissions": [
      "offers:read", "offers:write", "offers:delete",
      "profiles:read", "profiles:write",
      "notifications:read", "notifications:write", "notifications:delete",
      "analytics:read", "analytics:write",
      "config:read", "config:write"
    ]
  },
  "analyst": {
    "permissions": [
      "offers:read",
      "profiles:read",
      "notifications:read",
      "analytics:read"
    ]
  },
  "service_account": {
    "permissions": [
      "offers:read", "offers:write",
      "profiles:read", "profiles:write",
      "notifications:write"
    ]
  },
  "customer": {
    "permissions": [
      "offers:read_own",
      "profiles:read_own",
      "notifications:read_own"
    ]
  }
}

# Default deny
default allow := false

# Allow if the principal has the required permission
allow if {
  role := input.principal.role
  permission := input.action
  permission in roles[role].permissions
}

# Customers can only read their own resources
allow if {
  input.principal.role == "customer"
  input.principal.customer_id == input.resource.customer_id
  permission_is_own_read(input.action)
}

permission_is_own_read(action) if {
  action in ["offers:read_own", "profiles:read_own", "notifications:read_own"]
}

# Helper: check if caller is admin
is_admin if {
  input.principal.role == "admin"
}

# Helper: check if caller is service account
is_service_account if {
  input.principal.role == "service_account"
}
