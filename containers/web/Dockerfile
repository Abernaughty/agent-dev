# Web Development Container (Svelte) — Alpine hardened
FROM node:22-alpine

# VS Code Server & npm prereqs (install once)
RUN apk update && apk upgrade && apk add --no-cache \
    bash curl git ca-certificates libstdc++ tar xz \
 && rm -rf /var/cache/apk/*

# Non-root user
RUN adduser -D -s /bin/bash -u 1500 developer

# Workspace + mountpoints (mkdir so Docker doesn't need to create inside RO bind)
RUN mkdir -p /workspace /workspace/node_modules \
 && chown -R developer:developer /workspace

# Entrypoint script (you had this earlier—put it back)
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

USER developer
WORKDIR /workspace
ENV SHELL=/bin/bash

EXPOSE 3000
ENTRYPOINT ["/entrypoint.sh"]
