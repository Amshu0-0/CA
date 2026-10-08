#!/usr/bin/env bash

# Creates the Secret "mongo-credentials" (the MongoDB admin username and password).
#
# Why a script and not a YAML file? A Secret manifest would put the password in git.
# Here the password is made on your computer, kept in .secrets/ (ignored by git)
# and sent to the cluster from a file, so it is never in the repository,
# never typed on a command line, and never printed.
#
# It is safe to run again: if the Secret already exists it is left alone,
# because MongoDB only creates its admin user the first time it starts,
# so changing the Secret later would lock the applications out of the database.

set -euo pipefail

NS="${NS:-ca2}"
KUBECTL="${KUBECTL:-kubectl}"

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SECRETS_DIR="$ROOT/.secrets"

# The namespace must exist before a Secret can be put into it.
"$KUBECTL" apply -f "$ROOT/namespace.yaml" >/dev/null

# If the Secret already exists, leave it unchanged.
if "$KUBECTL" -n "$NS" get secret mongo-credentials >/dev/null 2>&1; then
  echo "Secret mongo-credentials already exists in namespace $NS."
  exit 0
fi

# Files created below should only be readable by the current user.
umask 077
mkdir -p "$SECRETS_DIR"

# Use the files in .secrets/ if they already exist; otherwise create them.
[ -s "$SECRETS_DIR/mongo-username" ] || \
  printf '%s' "ca2admin" > "$SECRETS_DIR/mongo-username"

[ -s "$SECRETS_DIR/mongo-password" ] || \
  openssl rand -base64 36 | tr -d '\n=+/' > "$SECRETS_DIR/mongo-password"

# Create the Kubernetes Secret using the credential files.
"$KUBECTL" -n "$NS" create secret generic mongo-credentials \
  --from-file=username="$SECRETS_DIR/mongo-username" \
  --from-file=password="$SECRETS_DIR/mongo-password"

"$KUBECTL" -n "$NS" label secret mongo-credentials \
  app.kubernetes.io/part-of=ca2-pipeline \
  --overwrite >/dev/null

echo "Created mongo-credentials Secret."
