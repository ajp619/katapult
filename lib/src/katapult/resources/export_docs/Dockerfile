ARG UBUNTU_RELEASE=24.04
FROM ubuntu:${UBUNTU_RELEASE}

ARG QUARTO_VERSION=1.7.27

# hadolint ignore=DL3008
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      ca-certificates curl gdebi-core \
    && curl -fsSLo /tmp/quarto.deb \
        "https://github.com/quarto-dev/quarto-cli/releases/download/v${QUARTO_VERSION}/quarto-${QUARTO_VERSION}-linux-amd64.deb" \
    && gdebi --non-interactive /tmp/quarto.deb \
    && rm -f /tmp/quarto.deb \
    && rm -rf /var/lib/apt/lists/*

RUN quarto --version

USER 65534:65534

WORKDIR /work
