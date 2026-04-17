package bankoffer.data_access

import future.keywords.if
import future.keywords.in

# PII fields that must be masked for non-admin roles
pii_fields := {
  "full_name",
  "date_of_birth",
  "national_id",
  "account_number",
  "phone_number",
  "email",
  "address"
}

# Fields allowed in aggregated analytics (non-PII)
analytics_fields := {
  "customer_id",
  "life_stage",
  "risk_score",
  "income_bracket",
  "spending_category",
  "offer_accepted",
  "product_type",
  "created_at"
}

# Default deny data access
default allow_data_access := false

# Admins can access all data
allow_data_access if {
  input.principal.role == "admin"
}

# Service accounts can access non-PII customer data
allow_data_access if {
  input.principal.role == "service_account"
  not contains_pii_request(input.requested_fields)
}

# Analysts can access aggregated analytics only
allow_data_access if {
  input.principal.role == "analyst"
  all_fields_analytics(input.requested_fields)
}

# Customers can access their own full data
allow_data_access if {
  input.principal.role == "customer"
  input.principal.customer_id == input.resource_owner_id
}

# Helper: check if requested fields contain PII
contains_pii_request(fields) if {
  field := fields[_]
  field in pii_fields
}

# Helper: check if all fields are analytics-safe
all_fields_analytics(fields) if {
  field := fields[_]
  field in analytics_fields
}

# Masking rules: return masked version of customer record for non-admin
masked_response(record) := result if {
  input.principal.role != "admin"
  result := {field: mask_value(record[field], field) | field := object.keys(record)[_]}
}

mask_value(_, field) := "***MASKED***" if {
  field in pii_fields
}

mask_value(value, field) := value if {
  not field in pii_fields
}
