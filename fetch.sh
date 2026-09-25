#!/bin/sh
set -e
cd "$(dirname "$0")"
mkdir -p src
grep -v '^#' designs.txt | while read name repo commit top files; do
  [ -z "$name" ] && continue
  d=src/$(basename "$repo")
  [ -d "$d" ] || git clone -q "$repo" "$d"
  git -C "$d" checkout -q "$commit"
done
