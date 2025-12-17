# Azure Functions Development Container
FROM mcr.microsoft.com/azure-functions/node:4-node18

# Install additional tools
RUN apt-get update && apt-get install -y \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN groupadd -g 1000 funcuser && \
    useradd -m -s /bin/bash -g funcuser -u 1000 funcuser

# Install Azure Functions Core Tools globally
RUN npm install -g azure-functions-core-tools@4 --unsafe-perm true

# Set up workspace
RUN mkdir -p /workspace && \
    chown -R funcuser:funcuser /workspace

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

USER funcuser
WORKDIR /workspace

EXPOSE 7071

ENTRYPOINT ["/entrypoint.sh"]
CMD ["start"]
