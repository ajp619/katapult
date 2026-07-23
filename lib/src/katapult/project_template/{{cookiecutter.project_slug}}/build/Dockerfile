ARG UBUNTU_RELEASE=1

FROM ubuntu:${UBUNTU_RELEASE}

ARG USERNAME
ARG UID_
ARG USERGROUP
ARG GID_
ARG QUARTO_VERSION="1.7.27"

#SHELL ["sh", "-exc"]

# set up default user and project directories
RUN <<EOF
userdel --remove ubuntu
groupadd -g ${GID_} ${USERGROUP}
useradd -m -l -u ${UID_} -g ${GID_} ${USERNAME}
mkdir /content && chown ${USERNAME}:${USERGROUP} /content
mkdir -p /content/nbk/.venv && chown ${USERNAME}:${USERGROUP} /content/nbk/.venv
mkdir -p /tmp/supervisor/log && chown ${USERNAME}:${USERGROUP} /tmp/supervisor
EOF

# Ensure apt-get doesn't open a menu on you.
ENV DEBIAN_FRONTEND=noninteractive

# add basic system essentials
# hadolint ignore=DL3008
RUN <<EOF
apt-get update -qy
apt-get install -qy --no-install-recommends --no-install-suggests \
    ca-certificates \
    curl \
    git \
    gnupg2 \
    lsb-release \
    supervisor \
    tree \
    ubuntu-keyring \
    vim
rm -rf /var/lib/apt/lists/*
EOF

# download and install latest nginx
SHELL ["/bin/bash", "-o", "pipefail", "-c"]
# hadolint ignore=DL3008
RUN <<EOF
curl https://nginx.org/keys/nginx_signing.key \
    | gpg --dearmor \
    | tee /usr/share/keyrings/nginx-archive-keyring.gpg >/dev/null
echo "deb [signed-by=/usr/share/keyrings/nginx-archive-keyring.gpg] http://nginx.org/packages/ubuntu $(lsb_release -cs) nginx" \
    | tee /etc/apt/sources.list.d/nginx.list
printf 'Package: *\nPin: origin nginx.org\nPin: release o=nginx\nPin-Priority: 900\n' \
    | tee /etc/apt/preferences.d/99nginx
apt-get update \
&& apt-get install -qy --no-install-recommends --no-install-suggests nginx \
&& rm -rf /var/lib/apt/lists/*
EOF

# install quarto
WORKDIR /tmp
RUN curl -o quarto-linux-amd64.deb -L https://github.com/quarto-dev/quarto-cli/releases/download/v${QUARTO_VERSION}/quarto-${QUARTO_VERSION}-linux-amd64.deb
RUN apt-get update -qy \
    && apt-get install -y --no-install-recommends /tmp/quarto-linux-amd64.deb \
    && rm -f /tmp/quarto-linux-amd64.deb \
    && rm -rf /var/lib/apt/lists/*

# configure nginx
COPY ./build/conf/nginx/default.conf /etc/nginx/conf.d/
COPY ./build/conf/nginx/location_blocks/*.conf /etc/nginx/conf.d/location_blocks/

# add uv executables from official site
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

# copy content
COPY --chown=${USERNAME}:${USERGROUP} . /content/

# configure supervisor
COPY ./build/conf/supervisor/supervisord.conf /etc/supervisor/supervisord.conf
COPY ./build/conf/supervisor/conf.d/* /etc/supervisor/conf.d/

# - Silence uv complaining about not being able to use hard links,
# - tell uv to byte-compile packages for faster application startups,
ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 

# copy jupyter configuration files
COPY --chown=${USERNAME}:${USERNAME}    \
     build/conf/nbk/config.jupyter  \
     /home/${USERNAME}/.jupyter


WORKDIR /content


RUN chown -R ${USERNAME}:${USERNAME} /var/cache/nginx \
    && chown -R ${USERNAME}:${USERNAME} /var/log/nginx \
    && chown -R ${USERNAME}:${USERNAME} /etc/nginx/conf.d \
    && chown -R ${USERNAME}:${USERNAME} /etc/supervisor/conf.d \
    && touch /var/run/nginx.pid \
    && chown -R ${USERNAME}:${USERNAME} /var/run/nginx.pid

USER ${USERNAME}
SHELL ["/bin/bash", "-c"]
ENV SHELL=/bin/bash

# Bake per-service virtualenvs into the image so the container is self-contained
# (no runtime `uv run` sync into a writable/networked .venv). What gets baked is
# governed by conf.d file-state (enabled = *.conf), matching what supervisor runs.
# lib/ is always baked when present (shared library + working dir for
# command-override consumers). SERVICES is an OPTIONAL override: when passed it is
# validated and used to reconcile conf.d for this build only (thin per-service
# images, e.g. `katx build -b SERVICES=api`); when unset, the committed conf.d
# state governs.
ARG SERVICES=""
RUN --mount=type=cache,target=/home/${USERNAME}/.cache/uv,uid=${UID_},gid=${GID_} <<'EOF'
set -euo pipefail
shopt -s nullglob
confd=/etc/supervisor/conf.d

# Programs present now (enabled .conf + disabled .conf_), used to validate an override.
present=( "$confd"/*.conf "$confd"/*.conf_ )
all_progs=""
if [ ${#present[@]} -gt 0 ]; then
  all_progs=$(sed -n 's/^\[program:\(.*\)\]$/\1/p' "${present[@]}" | sort -u | tr '\n' ' ')
fi

# OVERRIDE PATH: only when SERVICES is passed. Validate, then reconcile conf.d
# (mutates the image's copied conf.d only, never the repo).
if [ -n "${SERVICES}" ]; then
  for tok in ${SERVICES//,/ }; do
    case " $all_progs " in *" $tok "*) : ;; *) echo "unknown service '$tok'; valid: $all_progs" >&2; exit 1 ;; esac
  done
  want=",${SERVICES},"
  for f in "${present[@]}"; do
    base=${f%.conf_}; base=${base%.conf}; name=${base##*/}
    case "$want" in
      *",$name,"*) [ -f "$base.conf"  ] || mv "$f" "$base.conf"  ;;
      *)           [ -f "$base.conf_" ] || mv "$f" "$base.conf_" ;;
    esac
  done
fi

# DEFAULT PATH: bake dirs from whatever is enabled (*.conf) plus lib. Skip any dir
# lacking pyproject.toml + uv.lock (e.g. freshly generated, unpopulated projects).
enabled=( "$confd"/*.conf )
dirs=""
if [ ${#enabled[@]} -gt 0 ]; then
  dirs=$(sed -n 's/^directory=//p' "${enabled[@]}" | sort -u)
fi
for d in $dirs /content/lib; do
  if [ -f "$d/pyproject.toml" ] && [ -f "$d/uv.lock" ]; then
    (cd "$d" && uv sync --frozen)
  fi
done
EOF

CMD ["/usr/bin/supervisord", "-c", "/etc/supervisor/supervisord.conf"]

