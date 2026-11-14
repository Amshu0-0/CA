#!/usr/bin/env bash
# Creates the SSH key pair for the cluster machines the first time, and leaves it alone after that.
# The private key stays in .secrets/ (which git ignores). Terraform uploads only the public key.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
KEY="$ROOT/.secrets/nlp-key"

# Files created below are readable only by the current user.
umask 077
mkdir -p "$ROOT/.secrets"

if [ -s "$KEY" ] && [ -s "$KEY.pub" ]; then
  echo "SSH key already exists: .secrets/nlp-key"
  exit 0
fi

rm -f "$KEY" "$KEY.pub"
ssh-keygen -q -t ed25519 -N "" -C "nlp-cluster" -f "$KEY"
echo "Created SSH key: .secrets/nlp-key (private) and .secrets/nlp-key.pub (public)"
