#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$HERE/vendor" "$HERE/bin"
if [ ! -d "$HERE/vendor/skill-up/.git" ]; then
  git clone https://github.com/alibaba/skill-up "$HERE/vendor/skill-up"
fi
cd "$HERE/vendor/skill-up"
git checkout fd0bcf2ab1c45e4ae449fd3c1dc5bd82f7196875
if git apply --check "$HERE/skill-up-tmpdir.patch"; then
  git apply "$HERE/skill-up-tmpdir.patch"
else
  git apply --reverse --check "$HERE/skill-up-tmpdir.patch"
fi
go build -o "$HERE/bin/skill-up" ./cmd/skill-up
