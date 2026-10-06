#!/usr/bin/env bash
# Installs the poller of one product as a `systemd --user` service (GH-176).
#
# usage: scripts/install-poller.sh <product-dir> [<stage0-dir>] [--print]
#        stage0-dir: the repo this script lives in by default
#        --print:    write the rendered unit to stdout and do nothing else
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
args=()
for arg in "$@"; do
    if [ "$arg" = "--print" ]; then
        print_only=1
    else
        args+=("$arg")
    fi
done
if [ "${#args[@]}" -lt 1 ] || [ "${#args[@]}" -gt 2 ]; then
    echo "usage: $0 <product-dir> [<stage0-dir>] [--print]" >&2
    exit 2
fi

here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
product="$(realpath "${args[0]}")"
stage0="$(realpath "${args[1]:-$here}")"

if [ ! -d "$product" ]; then
    echo "install-poller: product dir not found: $product" >&2
    exit 1
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

template="$here/scripts/systemd/agentic-poll.service.in"
# `|` as the sed delimiter: paths carry `/`. The template quotes both directories for
# systemd, so spaces are fine. A path carrying `|`, `&`, `"`, `\`, `$`, `%` or a newline
# would break the rendering or systemd's own quoting, so it is refused instead of escaped.
for value in "$product" "$stage0" "$path"; do
    case "$value" in
        *[\|\&\"\\\$]* | *$'\n'* | *%*)
            echo "install-poller: refusing a path with | & \" \\ \$ % or a newline: $value" >&2
            exit 1
            ;;
    esac
done
rendered="$(sed -e "s|@STAGE0@|$stage0|g" -e "s|@PRODUCT_DIR@|$product|g" -e "s|@PATH@|$path|g" "$template")"

if [ "$print_only" -eq 1 ]; then
    printf '%s\n' "$rendered"
    exit 0
fi

unit="agentic-poll-$(systemd-escape --path "$product").service"
target="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
mkdir -p "$target"
printf '%s\n' "$rendered" > "$target/$unit"
systemctl --user daemon-reload
systemctl --user enable --now "$unit"

echo "installed $target/$unit and started it"
echo "to start it at boot without a login, run once:  loginctl enable-linger $USER"
echo "logs:  journalctl --user -u $unit"
