# Unified Dev Container for osm-geocoder
# Environment: Ubuntu 24.04, JDK 26, Maven, Git, Opencode, Antigravity CLI (agy)
# Timezone: Europe/Berlin
# User: ubuntu (UID 1000)
FROM ubuntu:24.04

USER root

ENV LANG=C.UTF-8
ENV LC_ALL=C.UTF-8
ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=Europe/Berlin

# 1. Base system tools, timezone & Maven
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
      ca-certificates \
      curl \
      git \
      maven \
      osmium-tool \
      python3 \
      python3-yaml \
      tar \
      unzip \
      tzdata && \
    ln -fs /usr/share/zoneinfo/Europe/Berlin /etc/localtime && \
    dpkg-reconfigure --frontend noninteractive tzdata && \
    rm -rf /var/lib/apt/lists/*

# 2. JDK 26 Installation
RUN set -eux; \
    ARCH="$(uname -m)"; \
    if [ "$ARCH" = "aarch64" ] || [ "$ARCH" = "arm64" ]; then \
        JDK_URL="https://download.oracle.com/java/26/latest/jdk-26_linux-aarch64_bin.tar.gz"; \
    else \
        JDK_URL="https://download.oracle.com/java/26/latest/jdk-26_linux-x64_bin.tar.gz"; \
    fi; \
    curl -sSL "$JDK_URL" -o /tmp/jdk26.tar.gz && \
    mkdir -p /opt/jdk26 && \
    tar -xzf /tmp/jdk26.tar.gz -C /opt/jdk26 --strip-components=1 && \
    rm /tmp/jdk26.tar.gz

ENV JAVA_HOME=/opt/jdk26
ENV PATH="$JAVA_HOME/bin:$PATH"

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
