# Web Development Container (Svelte + VS Code Server)
FROM node:18-alpine

# Install security updates and VS Code Server dependencies
RUN apk update && apk upgrade && \
    apk add --no-cache \
    git \
    curl \
    bash \
    sudo \
    shadow

# Create non-root user
RUN addgroup -g 1000 developer && \
    adduser -D -s /bin/bash -G developer -u 1000 developer && \
    echo "developer ALL=(ALL) NOPASSWD:ALL" >> /etc/sudoers

# Install VS Code Server
USER developer
WORKDIR /home/developer

# Download and install code-server
RUN curl -fsSL https://code-server.dev/install.sh | sh

# Switch back to setup the workspace
USER root

# Create workspace directory with proper permissions
RUN mkdir -p /workspace && \
    chown -R developer:developer /workspace

# Install global npm packages commonly used in Svelte development
RUN npm install -g \
    @sveltejs/kit \
    vite \
    eslint \
    prettier

# Set up the entrypoint
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

USER developer
WORKDIR /workspace

EXPOSE 3000 8080

ENTRYPOINT ["/entrypoint.sh"]
CMD ["dev"]
