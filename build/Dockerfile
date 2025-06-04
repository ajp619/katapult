ARG UBUNTU_RELEASE=1

FROM ubuntu:${UBUNTU_RELEASE}

ARG USERNAME
ARG GROUPNAME
ARG QUARTO_VERSION="1.7.27"

#SHELL ["sh", "-exc"]

# set up default user and project directories
RUN <<EOF
userdel --remove ubuntu
useradd -m ${USERNAME}
mkdir /content && chown ${USERNAME}:${GROUPNAME} /content
mkdir -p /content/nbk/.venv && chown ${USERNAME}:${GROUPNAME} /content/nbk/.venv
mkdir -p /tmp/supervisor/log && chown ${USERNAME}:${GROUPNAME} /tmp/supervisor
EOF

# Ensure apt-get doesn't open a menu on you.
ENV DEBIAN_FRONTEND=noninteractive

# add basic system essentials
RUN <<EOF
apt-get update -qy
apt-get install -qy \
    -o APT::Install-Recommends=false \
    -o APT::Install-Suggests=false \
    ca-certificates \
    curl \
    git \
    gnupg2 \
    lsb-release \
    supervisor \
    tree \
    ubuntu-keyring \
    vim
EOF

# download and install latest nginx
RUN <<EOF
curl https://nginx.org/keys/nginx_signing.key \
    | gpg --dearmor \
    | tee /usr/share/keyrings/nginx-archive-keyring.gpg >/dev/null
echo "deb [signed-by=/usr/share/keyrings/nginx-archive-keyring.gpg] http://nginx.org/packages/ubuntu `lsb_release -cs` nginx" \
    | tee /etc/apt/sources.list.d/nginx.list
echo "Package: *\nPin: origin nginx.org\nPin: release o=nginx\nPin-Priority: 900\n" \
    | tee /etc/apt/preferences.d/99nginx
apt-get update \
&& apt-get install -qy \
    -o APT::Install-Recommends=false \
    -o APT::Install-Suggests=false \
    nginx \
&& rm -rf /var/lib/apt/lists/*
EOF

# install quarto
WORKDIR /tmp
RUN curl -o quarto-linux-amd64.deb -L https://github.com/quarto-dev/quarto-cli/releases/download/v${QUARTO_VERSION}/quarto-${QUARTO_VERSION}-linux-amd64.deb
RUN apt-get install /tmp/quarto-linux-amd64.deb && rm quarto-linux-amd64.deb

# configure nginx
COPY ./build/conf/nginx/default.conf /etc/nginx/conf.d/
COPY ./build/conf/nginx/location_blocks/*.conf /etc/nginx/conf.d/location_blocks/

# add uv executables from official site
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

# copy content
COPY --chown=${USERNAME}:${GROUPNAME} . /content/

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
    && touch /var/run/nginx.pid \
    && chown -R ${USERNAME}:${USERNAME} /var/run/nginx.pid

USER ${USERNAME}
SHELL ["/bin/bash", "-c"]
ENV SHELL=/bin/bash
CMD ["/usr/bin/supervisord", "-c", "/etc/supervisor/supervisord.conf"]

