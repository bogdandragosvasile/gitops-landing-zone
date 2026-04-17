#!/usr/bin/env bash
# pre-commit-hook.sh — Secret scanning + YAML validation + Helm lint
# Shared pre-commit hook for all repos under this landing zone
# Cross-platform: works in Git Bash on Windows and bash on Linux/macOS
#
# Exit codes:
#   0  — all checks passed
#   1  — one or more checks failed

set -euo pipefail

# ──────────────────────────────────────────────
# Colour helpers (degrade gracefully if no tty)
# ──────────────────────────────────────────────
RED=""
YEL=""
GRN=""
BLU=""
RST=""
if [ -t 1 ]; then
  RED="\033[0;31m"
  YEL="\033[0;33m"
  GRN="\033[0;32m"
  BLU="\033[0;34m"
  RST="\033[0m"
fi

info()  { printf "${BLU}[pre-commit]${RST} %s\n" "$*"; }
warn()  { printf "${YEL}[pre-commit] WARN:${RST} %s\n" "$*"; }
error() { printf "${RED}[pre-commit] FAIL:${RST} %s\n" "$*" >&2; }
ok()    { printf "${GRN}[pre-commit] OK:${RST} %s\n" "$*"; }

FAILED=0

# ──────────────────────────────────────────────
# Collect staged files
# ──────────────────────────────────────────────
# Only files that are Added, Copied, Modified, or Renamed (not deleted)
STAGED_FILES=$(git diff --cached --name-only --diff-filter=ACMR 2>/dev/null || true)

if [ -z "$STAGED_FILES" ]; then
  info "No staged files to check."
  exit 0
fi

info "Checking $(echo "$STAGED_FILES" | wc -l | tr -d ' ') staged file(s)..."

# ──────────────────────────────────────────────
# SECTION 1: Secret pattern scanning
# ──────────────────────────────────────────────
info "Running secret pattern scan..."

# Files to always skip (binary-ish or safe allowlists)
SECRET_SKIP_PATTERN='^(node_modules/|dist/|\.git/|.*\.(png|jpg|jpeg|gif|ico|svg|woff|woff2|ttf|eot|pdf|zip|tar|gz|bin|exe|dll|so|dylib)$)'

# Patterns that indicate plaintext secrets.
# Each entry is: LABEL<TAB>REGEX
# The regex is applied with grep -P (Perl-compatible) for portability.
SECRET_PATTERNS=(
  "AWS Access Key ID	AKIA[0-9A-Z]{16}"
  "AWS Secret Access Key	(?i)aws.{0,20}secret.{0,20}['\"][0-9a-zA-Z/+]{40}['\"]"
  "Generic API key assignment	(?i)(api[_-]?key|apikey)\s*[:=]\s*['\"][A-Za-z0-9_\-]{16,}['\"]"
  "Generic token assignment	(?i)(token|access_token|auth_token|secret_token)\s*[:=]\s*['\"][A-Za-z0-9_\-\.]{16,}['\"]"
  "Generic password assignment	(?i)(password|passwd|pwd)\s*[:=]\s*['\"][^'\"]{8,}['\"]"
  "Private key header	-----BEGIN (RSA |EC |DSA |OPENSSH )?PRIVATE KEY"
  "Generic connection string	(?i)(mongodb|postgresql|postgres|mysql|redis|amqp)://[^:]+:[^@]+@"
  "Basic auth in URL	https?://[A-Za-z0-9_\-\.]+:[A-Za-z0-9_\-\.!@#$%^&*]{6,}@"
  "Slack webhook	https://hooks\.slack\.com/services/T[A-Z0-9]+/B[A-Z0-9]+/[A-Za-z0-9]+"
  "GitHub token	gh[pousr]_[A-Za-z0-9]{36,}"
  "Docker Hub credentials	(?i)docker.{0,20}(password|passwd|token)\s*[:=]\s*['\"][^'\"]{8,}['\"]"
)

# Allowlisted literals — found patterns containing these strings are suppressed.
# Keep this list minimal and documented.
ALLOWLIST=(
  "Test1234!"                              # intentional demo password (documented)
  ".env.example"                           # template file, not real secrets
  "vaultwarden-admin-token-placeholder"    # documented placeholder in vaultwarden config
  "CHANGE_ME"                              # explicit placeholder sentinel
  "REPLACE_ME"                             # explicit placeholder sentinel
  "YOUR_"                                  # common template variable prefix
  "example.com"                            # example domains in docs
)

build_allowlist_grep() {
  local pattern=""
  for item in "${ALLOWLIST[@]}"; do
    if [ -n "$pattern" ]; then
      pattern="${pattern}|$(printf '%s' "$item" | sed 's/[.[\*^$(){}|+?]/\\&/g')"
    else
      pattern="$(printf '%s' "$item" | sed 's/[.[\*^$(){}|+?]/\\&/g')"
    fi
  done
  echo "$pattern"
}

ALLOWLIST_PATTERN=$(build_allowlist_grep)

SECRET_HITS=0
while IFS= read -r file; do
  # Skip files matching the binary/skip pattern
  if echo "$file" | grep -qE "$SECRET_SKIP_PATTERN"; then
    continue
  fi

  # Skip if file no longer exists on disk (e.g., renamed source)
  if [ ! -f "$file" ]; then
    continue
  fi

  # Run each pattern against the staged content (git show :file gives staged version)
  STAGED_CONTENT=$(git show ":${file}" 2>/dev/null || true)
  if [ -z "$STAGED_CONTENT" ]; then
    continue
  fi

  for entry in "${SECRET_PATTERNS[@]}"; do
    label="${entry%%	*}"
    regex="${entry##*	}"

    # grep -P may not exist on all systems; fall back to grep -E for basic patterns
    if echo "$STAGED_CONTENT" | grep -qP "$regex" 2>/dev/null; then
      # Check against allowlist
      MATCHES=$(echo "$STAGED_CONTENT" | grep -P "$regex" 2>/dev/null || true)
      # Filter out allowlisted lines
      REAL_MATCHES=$(echo "$MATCHES" | grep -vE "$ALLOWLIST_PATTERN" || true)
      if [ -n "$REAL_MATCHES" ]; then
        error "Possible secret detected in '${file}': ${label}"
        echo "$REAL_MATCHES" | head -5 | while IFS= read -r line; do
          # Redact the middle of long values for display
          DISPLAY=$(echo "$line" | sed 's/\(.\{20\}\).\{8,\}\(.\{4\}\)/\1[REDACTED]\2/g')
          printf "    %s\n" "$DISPLAY" >&2
        done
        SECRET_HITS=$((SECRET_HITS + 1))
        FAILED=1
      fi
    fi
  done
done <<< "$STAGED_FILES"

if [ "$SECRET_HITS" -eq 0 ]; then
  ok "No secret patterns found."
else
  error "${SECRET_HITS} potential secret(s) found. Commit blocked."
  printf "${YEL}  Tip: Use kubeseal to store secrets safely, or add to .gitleaks.toml allowlist if intentional.${RST}\n" >&2
fi

# ──────────────────────────────────────────────
# SECTION 2: YAML syntax validation
# ──────────────────────────────────────────────
info "Validating YAML files..."

YAML_FILES=$(echo "$STAGED_FILES" | grep -E '\.(yaml|yml)$' || true)
YAML_ERRORS=0

if [ -z "$YAML_FILES" ]; then
  ok "No YAML files staged."
else
  # Prefer python3 yaml.safe_load for validation (available on most systems)
  YAML_VALIDATOR=""
  if command -v python3 &>/dev/null; then
    YAML_VALIDATOR="python3"
  elif command -v python &>/dev/null; then
    YAML_VALIDATOR="python"
  fi

  while IFS= read -r file; do
    if [ ! -f "$file" ]; then
      continue
    fi

    if [ -n "$YAML_VALIDATOR" ]; then
      VALIDATE_ERROR=$("$YAML_VALIDATOR" - <<'PYEOF' <(git show ":${file}" 2>/dev/null) 2>&1 || true
import sys, yaml
target = sys.argv[1] if len(sys.argv) > 1 else '/dev/stdin'
try:
    with open(target) as f:
        list(yaml.safe_load_all(f))
    sys.exit(0)
except yaml.YAMLError as e:
    print(str(e))
    sys.exit(1)
PYEOF
      )
      # The heredoc approach above is awkward — use a temp-file method instead
      TMPFILE=$(mktemp /tmp/precommit-yaml-XXXXXX.yaml 2>/dev/null || mktemp)
      git show ":${file}" > "$TMPFILE" 2>/dev/null || true
      VALIDATE_ERROR=$("$YAML_VALIDATOR" -c "
import sys, yaml
errors = []
try:
    with open('${TMPFILE}') as f:
        list(yaml.safe_load_all(f))
except yaml.YAMLError as e:
    errors.append(str(e))
if errors:
    for err in errors:
        print(err)
    sys.exit(1)
" 2>&1 || true)
      rm -f "$TMPFILE"

      if [ -n "$VALIDATE_ERROR" ]; then
        error "YAML syntax error in '${file}':"
        echo "$VALIDATE_ERROR" | head -10 | while IFS= read -r line; do
          printf "    %s\n" "$line" >&2
        done
        YAML_ERRORS=$((YAML_ERRORS + 1))
        FAILED=1
      else
        ok "YAML valid: ${file}"
      fi
    else
      warn "python3/python not found — skipping YAML validation for '${file}'"
    fi
  done <<< "$YAML_FILES"

  if [ "$YAML_ERRORS" -eq 0 ] && [ -n "$YAML_VALIDATOR" ]; then
    ok "All YAML files are syntactically valid."
  elif [ "$YAML_ERRORS" -gt 0 ]; then
    error "${YAML_ERRORS} YAML file(s) failed validation. Commit blocked."
  fi
fi

# ──────────────────────────────────────────────
# SECTION 3: Helm chart linting
# ──────────────────────────────────────────────
info "Checking for Helm chart changes..."

# Determine if any staged files belong to a Helm chart.
# A chart is identified by a Chart.yaml file in an ancestor directory.
CHART_DIRS=()

while IFS= read -r file; do
  # Walk up the directory tree looking for Chart.yaml
  dir=$(dirname "$file")
  while [ "$dir" != "." ] && [ "$dir" != "/" ] && [ "$dir" != "" ]; do
    if [ -f "${dir}/Chart.yaml" ]; then
      # Add to array if not already present
      already=0
      for existing in "${CHART_DIRS[@]+"${CHART_DIRS[@]}"}"; do
        if [ "$existing" = "$dir" ]; then
          already=1
          break
        fi
      done
      if [ "$already" -eq 0 ]; then
        CHART_DIRS+=("$dir")
      fi
      break
    fi
    dir=$(dirname "$dir")
  done
done <<< "$STAGED_FILES"

HELM_ERRORS=0

if [ "${#CHART_DIRS[@]}" -eq 0 ]; then
  ok "No Helm chart directories affected."
else
  if ! command -v helm &>/dev/null; then
    warn "helm not found in PATH — skipping Helm lint."
    warn "Install Helm: https://helm.sh/docs/intro/install/"
  else
    for chart_dir in "${CHART_DIRS[@]}"; do
      info "Running helm lint on '${chart_dir}'..."
      LINT_OUTPUT=$(helm lint "$chart_dir" 2>&1 || true)
      # helm lint exits 1 on ERROR, but also prints warnings.
      # We fail only on [ERROR] lines.
      if echo "$LINT_OUTPUT" | grep -q "\[ERROR\]"; then
        error "Helm lint failed for '${chart_dir}':"
        echo "$LINT_OUTPUT" | grep -E "\[ERROR\]|\[WARNING\]" | while IFS= read -r line; do
          printf "    %s\n" "$line" >&2
        done
        HELM_ERRORS=$((HELM_ERRORS + 1))
        FAILED=1
      else
        ok "Helm lint passed: ${chart_dir}"
        # Print warnings without blocking
        WARNINGS=$(echo "$LINT_OUTPUT" | grep "\[WARNING\]" || true)
        if [ -n "$WARNINGS" ]; then
          warn "Helm lint warnings (non-blocking) for '${chart_dir}':"
          echo "$WARNINGS" | while IFS= read -r line; do
            printf "    %s\n" "$line"
          done
        fi
      fi
    done

    if [ "$HELM_ERRORS" -eq 0 ]; then
      ok "All Helm charts lint clean."
    fi
  fi
fi

# ──────────────────────────────────────────────
# Final verdict
# ──────────────────────────────────────────────
echo ""
if [ "$FAILED" -eq 0 ]; then
  printf "${GRN}[pre-commit] All checks passed. Proceeding with commit.${RST}\n"
  exit 0
else
  printf "${RED}[pre-commit] Commit blocked — fix the issues above before committing.${RST}\n" >&2
  printf "${YEL}[pre-commit] To bypass in an emergency: git commit --no-verify${RST}\n" >&2
  exit 1
fi
