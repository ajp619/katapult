"""0004 -- let the image build fetch private git dependencies with a read-only key.

A project can depend on a private repository with a pinned git source, e.g.
``uv add "pkg @ git+ssh://git@host/org/repo.git@v1.0.0#subdirectory=lib"``. The image
build bakes venvs with ``uv sync --frozen``, so it needs the repository's read-only key
(``build/.git_ssh_credentials``). ``katx build`` hands that key to BuildKit as the
``git_ssh_key`` secret, only when the file exists and some ``uv.lock`` has a
git-over-ssh source; the secret is mounted for one step and never stored in a layer.

The template's venv-bake step mounts the secret itself. ``EnsureBlock`` can only insert,
so for existing projects this migration inserts a separate step after the supervisor
``conf.d`` copy: it installs ``openssh-client`` and pre-syncs, with the secret mounted,
only the envs whose ``uv.lock`` has a git-over-ssh source. The existing bake step then
has nothing left to fetch. Projects without such a source skip both steps, and a
project that has one but no key fails with a clear message. Host keys come from the
project's committed ``build/conf/ssh/known_hosts``.
"""

from __future__ import annotations

from katapult.migrations.base import EnsureBlock, Migration

MARKER = "id=git_ssh_key"

_DOCKERFILE_BLOCK = """
# Private git dependencies (katapult migration 0004). `katx build` passes the
# read-only key as the `git_ssh_key` secret only when a uv.lock has a git-over-ssh
# source. This pre-syncs just those envs with the secret mounted for this step
# only, so the venv bake below has nothing left to fetch; projects without such a
# source skip both steps. Host keys come from the committed build/conf/ssh/known_hosts.
RUN if grep -qs 'git = "ssh://' /content/*/uv.lock && ! command -v ssh >/dev/null; then \\
        apt-get update -qy \\
        && apt-get install -qy --no-install-recommends --no-install-suggests openssh-client \\
        && rm -rf /var/lib/apt/lists/*; \\
    fi
USER ${USERNAME}
RUN --mount=type=cache,target=/home/${USERNAME}/.cache/uv,uid=${UID_},gid=${GID_} \\
    --mount=type=secret,id=git_ssh_key,uid=${UID_},required=false <<'EOF'
set -euo pipefail
shopt -s nullglob
enabled=( /etc/supervisor/conf.d/*.conf )
dirs=""
if [ ${#enabled[@]} -gt 0 ]; then
  dirs=$(sed -n 's/^directory=//p' "${enabled[@]}" | sort -u)
fi
for d in $(printf '%s\\n' $dirs /content/lib | sort -u); do
  [ -f "$d/pyproject.toml" ] && grep -qs 'git = "ssh://' "$d/uv.lock" || continue
  if [ ! -f /run/secrets/git_ssh_key ]; then
    echo "error: $d/uv.lock has a git-over-ssh dependency but no build/.git_ssh_credentials was passed to the build (see the katapult docs: private git dependencies)" >&2
    exit 1
  fi
  (
    cd "$d"
    export GIT_SSH_COMMAND="ssh -i /run/secrets/git_ssh_key -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile=/content/build/conf/ssh/known_hosts"
    UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1 uv sync --frozen
  )
done
EOF
USER root
"""

migration = Migration(
    id="0004_private_git_deps",
    katapult_version="0.14.0",
    description="Let the image build fetch private git dependencies with a read-only key.",
    actions=[
        EnsureBlock(
            "build/Dockerfile",
            marker=MARKER,
            block=_DOCKERFILE_BLOCK,
            after="COPY ./build/conf/supervisor/conf.d/* /etc/supervisor/conf.d/",
            description="private git dependency pre-sync step",
        ),
    ],
)
