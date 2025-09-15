# MCP Shell Server
FROM node:18-alpine

# Security hardening
RUN apk update && apk upgrade && \
    apk add --no-cache dumb-init bash && \
    rm -rf /var/cache/apk/*

# Create non-root user
RUN adduser -D -s /bin/sh -u 1001 mcpuser

# Create app directory
WORKDIR /app

# Copy package files
COPY package*.json ./

# Install dependencies
RUN npm install --only=production && npm cache clean --force

# Copy application code
COPY . .

# Change ownership
RUN chown -R mcpuser:mcpuser /app

USER mcpuser

# Use dumb-init for proper signal handling
ENTRYPOINT ["dumb-init", "--"]
CMD ["node", "server.js"]
