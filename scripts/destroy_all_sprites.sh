#!/usr/bin/env bash
set -euo pipefail

sprites=$(sprite list -prefix proofloop 2>/dev/null)

if [ -z "$sprites" ]; then
  echo "No sprites found."
  exit 0
fi

count=$(echo "$sprites" | wc -l)
echo "Found $count sprite(s):"
echo "$sprites"
echo ""
read -rp "Destroy all? [y/N] " confirm
if [[ "$confirm" != [yY] ]]; then
  echo "Aborted."
  exit 0
fi

while IFS= read -r name; do
  [ -z "$name" ] && continue
  echo "Destroying $name ..."
  echo "y" | sprite destroy --force "$name" 2>&1 || echo "  Failed to destroy $name"
done <<< "$sprites"

echo "Done."
