FROM node:20-bullseye

# git for repo clones (some tasks might need it)
# ca-certificates for any HTTPS calls
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace

# node:20-bullseye already has a 'node' user at UID 1000. Reuse it.
USER node
