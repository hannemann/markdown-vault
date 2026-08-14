#!/bin/sh
# Print a pip --require-hashes lock for every wheel in <wheels-dir> to stdout:
# one "name==version --hash=sha256:..." per package, multi-platform wheels of the
# same package merged onto one line with several --hash= flags.
#
# Used by `make lock-wheels` (writes the committed requirements.lock) and
# `make download-wheels` (regenerates and diffs against the committed lock, so an
# upstream artifact change surfaces as a reviewable diff instead of a silent
# re-hash). Deterministic: entries sorted, so the diff is stable.
set -eu

dir="${1:?usage: gen-wheel-lock.sh <wheels-dir>}"

for whl in "$dir"/*.whl; do
    base=$(basename "$whl")
    name=$(echo "$base" | cut -d- -f1)
    ver=$(echo "$base" | cut -d- -f2)
    h=$(pip3 hash "$whl" | grep -oE 'sha256:[0-9a-f]+')
    echo "$name==$ver $h"
done | sort | awk '
    { key = $1
      if (key in H) H[key] = H[key] " --hash=" $2
      else { H[key] = "--hash=" $2; order[++n] = key } }
    END { for (i = 1; i <= n; i++) print order[i] " " H[order[i]] }'
