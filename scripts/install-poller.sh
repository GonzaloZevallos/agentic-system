#!/usr/bin/env bash
# Installs the poller of one product as a `systemd --user` service (GH-176).
#
# usage: scripts/install-poller.sh <product-dir> [<stage0-dir>] [--print] [--rotate]
#        stage0-dir: the repo this script lives in by default. An explicit one that is
#                    already a symlink keeps its link path in the render (not resolved)
#        --print:    write the rendered unit to stdout and do nothing else
#        --rotate:   stage0-dir must be the symlink <root>/current (root: ~/.agentic-stage0s).
#                    Also renders the machine-wide stage0 rotate service and timer, and
#                    installs and enables them next to the poller
#
# Needs no root: the unit goes under ~/.config/systemd/user and is enabled with
# `systemctl --user`. The service starts at boot, without a login, only once the user has
# run `loginctl enable-linger $USER` (this script says so; it does not do it for you).
#
# One unit per product, named after the product's absolute path. To stop and remove it:
#   systemctl --user disable --now <unit> && rm ~/.config/systemd/user/<unit> \
#     && systemctl --user daemon-reload

set -euo pipefail

print_only=0
rotate=0
args=()
for arg in "$@"; do
    if [ "$arg" = "--print" ]; then
        print_only=1
    elif [ "$arg" = "--rotate" ]; then
        rotate=1
    else
        args+=("$arg")
    fi
done
if [ "${#args[@]}" -lt 1 ] || [ "${#args[@]}" -gt 2 ]; then
    echo "usage: $0 <product-dir> [<stage0-dir>] [--print] [--rotate]" >&2
    exit 2
fi

here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
product="$(realpath "${args[0]}")"
# An explicit stage0 that is a symlink keeps its link path (made absolute, not resolved):
# the unit must follow `current` when a rotation moves it. The default (this checkout, no
# second argument) always resolves, as before: `pwd` may hand back a logical path.
link=""
if [ "${#args[@]}" -eq 2 ]; then
    given="${args[1]}"
    while [ "${#given}" -gt 1 ] && [ "${given%/}" != "$given" ]; do
        given="${given%/}"
    done
    if [ -L "$given" ]; then
        link="$(realpath -s "$given")"
        stage0="$link"
    else
        stage0="$(realpath "$given")"
    fi
else
    stage0="$(realpath "$here")"
fi

if [ ! -d "$product" ]; then
    echo "install-poller: product dir not found: $product" >&2
    exit 1
fi
root=""
if [ "$rotate" -eq 1 ]; then
    bootstrap="create the first stage: scripts/stage0.sh <ref> <root>/<sha>, run \`uv sync\` there, then ln -s <sha> <root>/current (root: $HOME/.agentic-stage0s)"
    if [ -z "$link" ] || [ "$(basename "$link")" != "current" ]; then
        echo "install-poller: --rotate needs the stage0 argument to be the symlink <root>/current; $bootstrap" >&2
        exit 1
    fi
    if [ ! -x "$link/.venv/bin/agentic" ]; then
        echo "install-poller: --rotate: $link/.venv/bin/agentic is not executable; $bootstrap" >&2
        exit 1
    fi
    root="$(dirname "$link")"
    # \`poll\` hard-codes the rotation root (agentic/cli.py), so it is not free until poll
    # gains --root.
    if [ "$(realpath "$root")" != "$(realpath -m "$HOME/.agentic-stage0s")" ]; then
        echo "install-poller: --rotate: $root is not the rotation root; the root must be $HOME/.agentic-stage0s while \`poll\` has no --root; $bootstrap" >&2
        exit 1
    fi
fi
if [ ! -x "$stage0/.venv/bin/agentic" ]; then
    echo "install-poller: $stage0/.venv/bin/agentic is not executable (run \`uv sync\` in the stage0 checkout)" >&2
    exit 1
fi

# The service has no login shell: the PATH it runs with is the one the tools were found
# on now. A tool that is missing now would only fail later, inside a run.
path=""
for tool in git gh uv claude; do
    found="$(command -v "$tool" || true)"
    if [ -z "$found" ]; then
        echo "install-poller: $tool not found on PATH" >&2
        exit 1
    fi
    dir="$(dirname "$found")"
    case ":$path:" in
        *":$dir:"*) ;;
        *) path="${path:+$path:}$dir" ;;
    esac
done
path="$path:/usr/bin:/bin"

# `|` as the sed delimiter: paths carry `/`. The template quotes both directories for
# systemd, so spaces are fine. A path carrying `|`, `&`, `"`, `\`, `$`, `%` or a newline
# would break the rendering or systemd's own quoting, so it is refused instead of escaped.
for value in "$product" "$stage0" "$root" "$path"; do
    case "$value" in
        *[\|\&\"\\\$]* | *$'\n'* | *%*)
            echo "install-poller: refusing a path with | & \" \\ \$ % or a newline: $value" >&2
            exit 1
            ;;
    esac
done

# Every template goes through here: lines starting with `## ` are notes for whoever reads
# the template and never reach a rendered unit.
render_unit() {
    sed -e '/^## /d' \
        -e "s|@STAGE0@|$stage0|g" -e "s|@PRODUCT_DIR@|$product|g" \
        -e "s|@PATH@|$path|g" -e "s|@ROOT@|$root|g" "$here/scripts/systemd/$1"
}

rendered="$(render_unit agentic-poll.service.in)"

if [ "$rotate" -eq 1 ]; then
    unit="agentic-poll-$(systemd-escape --path "$product").service"
    rotate_service="agentic-stage0-rotate.service"
    rotate_timer="agentic-stage0-rotate.timer"
    rendered_service="$(render_unit agentic-stage0-rotate.service.in)"
    rendered_timer="$(render_unit agentic-stage0-rotate.timer.in)"
fi

if [ "$print_only" -eq 1 ]; then
    if [ "$rotate" -eq 1 ]; then
        printf '# ---- %s ----\n%s\n' "$unit" "$rendered"
        printf '# ---- %s ----\n%s\n' "$rotate_service" "$rendered_service"
        printf '# ---- %s ----\n%s\n' "$rotate_timer" "$rendered_timer"
    else
        printf '%s\n' "$rendered"
    fi
    exit 0
fi

if [ "$rotate" -eq 0 ]; then
    unit="agentic-poll-$(systemd-escape --path "$product").service"
fi
target="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
mkdir -p "$target"
printf '%s\n' "$rendered" > "$target/$unit"
if [ "$rotate" -eq 1 ]; then
    printf '%s\n' "$rendered_service" > "$target/$rotate_service"
    printf '%s\n' "$rendered_timer" > "$target/$rotate_timer"
fi
systemctl --user daemon-reload
systemctl --user enable --now "$unit"
if [ "$rotate" -eq 1 ]; then
    systemctl --user enable --now "$rotate_timer"
fi

echo "installed $target/$unit and started it"
if [ "$rotate" -eq 1 ]; then
    echo "installed $target/$rotate_service and $target/$rotate_timer; the timer is enabled"
    echo "a poller that is already running needs:  systemctl --user restart $unit"
fi
echo "to start it at boot without a login, run once:  loginctl enable-linger $USER"
echo "logs:  journalctl --user -u $unit"
