#!/usr/bin/env bash
# Creates the SSH key pair for the cluster machines the first time, and leaves it alone after that.
# The private key stays in .secrets/ (which git ignores). Terraform uploads only the public key.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
KEY="$ROOT/.secrets/ca2-key"

# Files created below are readable only by the current user.
umask 077
mkdir -p "$ROOT/.secrets"

if [ -s "$KEY" ] && [ -s "$KEY.pub" ]; then
  echo "SSH key already exists: .secrets/ca2-key"
  exit 0
fi

rm -f "$KEY" "$KEY.pub"
ssh-keygen -q -t ed25519 -N "" -C "ca2-cluster" -f "$KEY"
echo "Created SSH key: .secrets/ca2-key (private) and .secrets/ca2-key.pub (public)"
