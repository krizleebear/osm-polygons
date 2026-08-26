# Unified Dev Container for osm-polygons
# Environment: Ubuntu 24.04, Eclipse Temurin JDK 21 (Adoptium), Maven, Git, Osmium Tool v1.19.0, Python Pyosmium, DuckDB, Opencode, Antigravity CLI (agy)
# Timezone: Europe/Berlin
# User: ubuntu (UID 1000)
FROM ubuntu:24.04

USER root

ENV LANG=C.UTF-8
ENV LC_ALL=C.UTF-8
ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=Europe/Berlin

# 1. Java Installation (Eclipse Temurin JDK 21 LTS, Adoptium)
COPY --from=eclipse-temurin:21-jdk /opt/java/openjdk /opt/java/openjdk
ENV JAVA_HOME=/opt/java/openjdk
ENV PATH="$JAVA_HOME/bin:$PATH"

# 2. Base system tools, timezone, Python osmium & Osmium Tool runtime libraries
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
      ca-certificates \
      curl \
      git \
      libboost-program-options1.83.0 \
      libbz2-1.0 \
      libexpat1 \
      liblz4-1 \
      maven \
      python3 \
      python3-pyosmium \
      python3-yaml \
      tar \
      unzip \
      zlib1g \
      tzdata && \
    ln -fs /usr/share/zoneinfo/Europe/Berlin /etc/localtime && \
    dpkg-reconfigure --frontend noninteractive tzdata && \
    rm -rf /var/lib/apt/lists/*

# Copy latest Osmium CLI Tool from official pipeline container (v1.19.1)
COPY --from=krizleebear/docker-osmium-tool:v1.19.1 /usr/bin/osmium /usr/local/bin/osmium

# 2b. Install DuckDB CLI + Spatial Extension (Multi-Arch)
RUN set -eux; \
    ARCH="$(uname -m)"; \
    if [ "$ARCH" = "aarch64" ] || [ "$ARCH" = "arm64" ]; then \
        DUCKDB_URL="https://github.com/duckdb/duckdb/releases/download/v1.5.5/duckdb_cli-linux-arm64.zip"; \
    else \
        DUCKDB_URL="https://github.com/duckdb/duckdb/releases/download/v1.5.5/duckdb_cli-linux-amd64.zip"; \
    fi; \
    curl -sSL "$DUCKDB_URL" -o /tmp/duckdb.zip && \
    unzip -q /tmp/duckdb.zip -d /usr/local/bin && \
    chmod 755 /usr/local/bin/duckdb && \
    rm /tmp/duckdb.zip && \
    duckdb -c "INSTALL spatial; LOAD spatial;" && \
    cp -r /root/.duckdb /tmp/.duckdb-ext-cache

# 3. Install AI Agent CLIs (opencode & agy)
RUN curl -fsSL https://opencode.ai/install | bash -s -- --no-modify-path && \
    mv /root/.opencode/bin/opencode /usr/local/bin/opencode && \
    chmod 755 /usr/local/bin/opencode && \
    rm -rf /root/.opencode

RUN curl -fsSL https://antigravity.google/cli/install.sh | bash -s -- --dir /usr/local/bin && \
    chmod 755 /usr/local/bin/agy

# 3b. Install GitHub CLI (gh 2.98.0, Multi-Arch)
RUN set -eux; \
    ARCH="$(uname -m)"; \
    if [ "$ARCH" = "aarch64" ] || [ "$ARCH" = "arm64" ]; then \
        GH_ARCH="linux_arm64"; \
    else \
        GH_ARCH="linux_amd64"; \
    fi; \
    GH_VERSION="2.98.0"; \
    curl -sSL "https://github.com/cli/cli/releases/download/v${GH_VERSION}/gh_${GH_VERSION}_${GH_ARCH}.tar.gz" -o /tmp/gh.tar.gz && \
    tar -xzf /tmp/gh.tar.gz -C /tmp && \
    mv /tmp/gh_${GH_VERSION}_${GH_ARCH}/bin/gh /usr/local/bin/gh && \
    chmod 755 /usr/local/bin/gh && \
    rm -rf /tmp/gh.tar.gz /tmp/gh_${GH_VERSION}_${GH_ARCH}

# 4. Create directories & user permissions
RUN install -d -o ubuntu -g ubuntu \
      /home/ubuntu/.config/opencode \
      /home/ubuntu/.local/share/opencode \
      /home/ubuntu/.local/state/opencode \
      /home/ubuntu/.config/antigravity \
      /home/ubuntu/.local/share/antigravity \
      /home/ubuntu/.local/state/antigravity \
      /home/ubuntu/.config/gh \
      /home/ubuntu/.gemini \
      /home/ubuntu/.m2

ENV HOME=/home/ubuntu

# Copy pre-installed DuckDB extensions (spatial) to ubuntu user
RUN cp -r /tmp/.duckdb-ext-cache /home/ubuntu/.duckdb && \
    chown -R ubuntu:ubuntu /home/ubuntu/.duckdb && \
    rm -rf /tmp/.duckdb-ext-cache

USER ubuntu
WORKDIR /app

ENTRYPOINT ["/bin/bash"]
