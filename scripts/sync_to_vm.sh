#!/usr/bin/env bash
#
# sync_to_vm.sh — copy this repository to a Multipass VM for real-Linux
# verification, the same way every prior development phase has done it
# by hand (see LOGS.md's Phase 3.6 entry for why: an earlier ad hoc
# transfer hit a race between `rm -rf` and `multipass transfer`, fixed
# by pausing and verifying removal first — both already applied below).
#
# This script exists so that exclusion list is defined in exactly one
# place, checked into git, instead of being retyped by hand on every
# transfer — which is exactly how a real secret (a local .env file)
# very nearly ended up inside a VM during Phase 7A (see LOGS.md).
#
# Usage:
#   scripts/sync_to_vm.sh <vm-name> [remote-dir]
#
# Example:
#   scripts/sync_to_vm.sh main-cattle nodeiq_test
#
# Never copies (see "Secrets Never Leave This Machine" in README.md):
#   .env             — the real OPENAI_API_KEY lives only here
#   .venv/           — a local virtualenv, rebuilt fresh on the VM
#   __pycache__/     — compiled bytecode, irrelevant on another machine
#   .pytest_cache/   — pytest's own local cache
#   snapshots/*.json — real system data from whoever ran a scan
#   .git/            — history doesn't need to travel for a smoke test

set -euo pipefail

VM_NAME="${1:?Usage: scripts/sync_to_vm.sh <vm-name> [remote-dir]}"
REMOTE_DIR="${2:-nodeiq_test}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STAGING_DIR="$(mktemp -d /tmp/nodeiq_sync.XXXXXX)"

cleanup() {
    rm -rf "$STAGING_DIR"
}
trap cleanup EXIT

echo "Staging a clean copy in $STAGING_DIR ..."
rsync -a \
    --exclude='.env' \
    --exclude='.venv/' \
    --exclude='__pycache__/' \
    --exclude='.pytest_cache/' \
    --exclude='snapshots/*.json' \
    --exclude='.git/' \
    --exclude='*.egg-info/' \
    "$REPO_ROOT/" "$STAGING_DIR/"

# Defense in depth: even though .env is excluded above, refuse to
# proceed if a secret-shaped file somehow ended up in the staging
# directory anyway, rather than silently transferring it.
if [ -e "$STAGING_DIR/.env" ]; then
    echo "Refusing to sync: $STAGING_DIR/.env exists. A .env file must never be transferred." >&2
    exit 1
fi

echo "Removing any previous copy at $VM_NAME:$REMOTE_DIR ..."
multipass exec "$VM_NAME" -- rm -rf "/home/ubuntu/$REMOTE_DIR"
sleep 2
if multipass exec "$VM_NAME" -- test -d "/home/ubuntu/$REMOTE_DIR"; then
    echo "Removal did not complete in time — aborting rather than racing the transfer." >&2
    exit 1
fi

echo "Transferring to $VM_NAME:$REMOTE_DIR ..."
multipass transfer -r "$STAGING_DIR" "$VM_NAME:$REMOTE_DIR"

echo "Done. On the VM:"
echo "  cd $REMOTE_DIR && python3 -m venv .venv && .venv/bin/pip install -e . && .venv/bin/pip install -r requirements.txt"
