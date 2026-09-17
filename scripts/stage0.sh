#!/usr/bin/env bash
# stage0: the checkout the system runs from, fixed to a sha (ADR-0017, issue #44).
#
# The system develops itself, and a run must never edit the code executing it: `agentic`
# runs from here and writes into the product, which may well be the repo this script
# came from. `preflight` refuses a run whose product is its own repository, and this is
# how you get the other copy.
#
# Updating stage0 is an explicit act: you name the commit. That is the point. A `git
# pull` that moved the running code mid-task is the failure this exists to prevent.
#
# A clone and not a `git worktree`: a worktree shares its repository with the checkout
# you are editing, so preflight would refuse it just the same, and it would share the
# toolchain that already bit gym-saas (#37).
#
# usage: scripts/stage0.sh <ref> [dir]     ref: a sha, a tag or origin/master
#                                          dir: ~/.agentic-stage0 by default

set -euo pipefail

ref="${1:-}"
dir="${2:-$HOME/.agentic-stage0}"
if [ -z "$ref" ]; then
    echo "usage: $0 <ref> [dir]   # ref: a sha, a tag or origin/master" >&2
    exit 2
fi

here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
origin="$(git -C "$here" remote get-url origin)"

if [ ! -e "$dir/.git" ]; then
    echo "cloning $origin into $dir"
    git clone --quiet "$origin" "$dir"
fi
git -C "$dir" fetch --quiet --tags origin
sha="$(git -C "$dir" rev-parse --verify "${ref}^{commit}")"
# Detached on purpose: stage0 is a commit, never a branch a pull could move under it.
git -C "$dir" checkout --quiet --detach "$sha"

# Without this uv warns that the active virtualenv is not this project's: whoever runs
# the script is likely inside the venv of the checkout they are editing.
(cd "$dir" && env -u VIRTUAL_ENV uv sync --quiet --locked)

echo
echo "stage0 at $dir, fixed to $(git -C "$dir" rev-parse --short HEAD)"
echo "run a task with:"
echo "    (cd $dir && uv run agentic run --issue N --product-dir $here)"
