FROM node:20-bookworm-slim

ARG INSTALL_CLOUD_TOOLS=0

RUN apt-get update && apt-get install -y --no-install-recommends \
    bash \
    ca-certificates \
    curl \
    fd-find \
    git \
    gnupg \
    iputils-ping \
    jq \
    less \
    locales \
    lsof \
    pkg-config \
    procps \
    python3 \
    python3-pip \
    python3-venv \
    pipx \
    ripgrep \
    shellcheck \
    shfmt \
    strace \
    tree \
    tzdata \
    unzip \
    wget \
    yamllint \
    build-essential \
    apt-transport-https \
    lsb-release \
    && rm -rf /var/lib/apt/lists/*

RUN if [ "$INSTALL_CLOUD_TOOLS" = "1" ]; then \
    install -m 0755 -d /etc/apt/keyrings && \
    curl -fsSL https://download.docker.com/linux/debian/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg && \
    chmod a+r /etc/apt/keyrings/docker.gpg && \
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/debian $(. /etc/os-release && echo $VERSION_CODENAME) stable" \
      > /etc/apt/sources.list.d/docker.list && \
    curl -fsSL https://pkgs.k8s.io/core:/stable:/v1.30/deb/Release.key | gpg --dearmor -o /etc/apt/keyrings/kubernetes-archive-keyring.gpg && \
    chmod a+r /etc/apt/keyrings/kubernetes-archive-keyring.gpg && \
    echo "deb [signed-by=/etc/apt/keyrings/kubernetes-archive-keyring.gpg] https://pkgs.k8s.io/core:/stable:/v1.30/deb/ /" \
      > /etc/apt/sources.list.d/kubernetes.list && \
    curl -fsSL https://packages.buildkite.com/helm-linux/helm-debian/gpgkey | gpg --dearmor -o /etc/apt/keyrings/helm.gpg && \
    chmod a+r /etc/apt/keyrings/helm.gpg && \
    echo "deb [signed-by=/etc/apt/keyrings/helm.gpg] https://packages.buildkite.com/helm-linux/helm-debian/any/ any main" \
      > /etc/apt/sources.list.d/helm-stable-debian.list && \
    apt-get update && apt-get install -y --no-install-recommends \
      docker-ce-cli \
      docker-compose-plugin \
      kubectl \
      helm \
    && rm -rf /var/lib/apt/lists/*; \
  fi

RUN ln -s /usr/bin/fdfind /usr/local/bin/fd

RUN mkdir -p /workspace && chown -R node:node /workspace

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

USER node
WORKDIR /workspace

ENTRYPOINT ["/entrypoint.sh"]
CMD ["sleep", "infinity"]
