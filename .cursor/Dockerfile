FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

# Base tools needed by Cursor plus the Python runtime used by this repository.
# The repository itself is cloned by Cursor; do not COPY source code into this image.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    ca-certificates \
    curl \
    git \
    gnupg \
    openssh-server \
    python3.12 \
    python3.12-venv \
    sudo \
    && rm -rf /var/lib/apt/lists/*

########################################################
# Docker-in-Docker support for docker compose services.
# Cursor documents fuse-overlayfs + iptables-legacy for
# more complex Docker workflows inside Cloud Agents.
########################################################
RUN install -m 0755 -d /etc/apt/keyrings && \
    curl --retry 3 --retry-delay 5 -fsSL https://download.docker.com/linux/ubuntu/gpg \
      | gpg --dearmor -o /etc/apt/keyrings/docker.gpg && \
    chmod a+r /etc/apt/keyrings/docker.gpg && \
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo \"$VERSION_CODENAME\") stable" \
      > /etc/apt/sources.list.d/docker.list && \
    apt-get update && \
    apt-get install -y --no-install-recommends \
      docker-ce=5:28.5.2-1~ubuntu.24.04~noble \
      docker-ce-cli=5:28.5.2-1~ubuntu.24.04~noble \
      containerd.io \
      docker-buildx-plugin \
      docker-compose-plugin \
      fuse-overlayfs \
      iptables \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /etc/docker && \
    printf '%s\n' '{' '  "storage-driver": "fuse-overlayfs"' '}' > /etc/docker/daemon.json && \
    update-alternatives --set iptables /usr/sbin/iptables-legacy && \
    update-alternatives --set ip6tables /usr/sbin/ip6tables-legacy

# Cursor Cloud Agents use an unprivileged Ubuntu user. Give it Docker access and
# passwordless sudo so the documented `sudo service docker start` startup works.
RUN id -u ubuntu >/dev/null 2>&1 || useradd -m -s /bin/bash ubuntu && \
    groupadd -f docker && \
    usermod -aG docker,sudo ubuntu && \
    echo 'ubuntu ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/ubuntu && \
    chmod 0440 /etc/sudoers.d/ubuntu

USER ubuntu
ENV HOME=/home/ubuntu
