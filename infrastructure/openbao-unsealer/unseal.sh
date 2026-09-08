#!/bin/sh
set -eu
BAO_ADDR="${BAO_ADDR:-http://openbao.openbao.svc:8200}"
KEYS_FILE=/keys/unseal-keys.yaml   # written by the initContainer (decrypted)
export BAO_ADDR
while true; do
  # bao status exit codes: 0=unsealed, 2=sealed, other=error/unreachable.
  # Treat anything but 0 as "needs unseal" (unseal is harmless when unreachable).
  if bao status >/dev/null 2>&1; then
    :   # unsealed, nothing to do
  else
    for k in $(grep '^unseal_key_' "$KEYS_FILE" | sed 's/.*:[[:space:]]*//'); do
      bao operator unseal "$k" >/dev/null 2>&1 || true
    done
    echo "unseal attempt done"
  fi
  sleep 10
done
