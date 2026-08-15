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

{
    for whl in "$dir"/*.whl; do
        [ -e "$whl" ] || continue
        # Wheel filenames escape the distribution name (python_pptx-...), so the
        # first two hyphen-separated fields are name and version.
        base=$(basename "$whl")
        name=$(echo "$base" | cut -d- -f1)
        ver=$(echo "$base" | cut -d- -f2)
        h=$(pip3 hash "$whl" | grep -oE 'sha256:[0-9a-f]+')
        echo "$name==$ver $h"
    done
    # Source dists (packages with no published wheel, e.g. odfpy). The sdist hash
    # is stable, so it survives --require-hashes; pip builds the wheel at
    # Flatpak-build time under --no-build-isolation using the SDK's setuptools.
    for sd in "$dir"/*.tar.gz; do
        [ -e "$sd" ] || continue
        # Unlike wheels, {name}-{version}.tar.gz keeps the name's hyphens, so
        # split on the LAST hyphen — a PEP 440 version never contains one.
        base=$(basename "$sd" .tar.gz)
        name=${base%-*}
        ver=${base##*-}
        h=$(pip3 hash "$sd" | grep -oE 'sha256:[0-9a-f]+')
        echo "$name==$ver $h"
    done
} | sort | awk '
    { key = $1
      if (key in H) H[key] = H[key] " --hash=" $2
      else { H[key] = "--hash=" $2; order[++n] = key } }
    END { for (i = 1; i <= n; i++) print order[i] " " H[order[i]] }'
