#!/bin/sh
set -eu
BAO_ADDR="${BAO_ADDR:-http://openbao.openbao.svc:8200}"
KEYS_FILE=/keys/unseal-keys.yaml   # written by the initContainer (decrypted)
while true; do
  sealed=$(bao status -address="$BAO_ADDR" -format=json 2>/dev/null | grep -o '"sealed":[a-z]*' | cut -d: -f2 || echo "true")
  if [ "$sealed" = "true" ]; then
    for k in $(grep '^unseal_key_' "$KEYS_FILE" | sed 's/.*:[[:space:]]*//'); do
      bao operator unseal -address="$BAO_ADDR" "$k" >/dev/null 2>&1 || true
    done
    echo "unseal attempt done"
  fi
  sleep 10
done
