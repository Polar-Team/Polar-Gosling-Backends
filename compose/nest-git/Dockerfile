# Stage 1: Builder - Only used to compile Caddy with plugins
FROM docker.io/library/caddy:2-builder AS builder

# Use xcaddy to build Caddy with cgi plugin
# caddy-cgi plugin allows Caddy to act as a CGI gateway
RUN xcaddy build \
  --with github.com/aksdb/caddy-cgi/v2

# Stage 2: Final runtime image
FROM docker.io/library/caddy:latest

# Install git (git-http-backend is bundled in the main git package on Alpine;
# git-daemon is the git:// protocol daemon and is not needed here).
# Create the caddy non-root user/group in the same layer — caddy:latest (Alpine
# base) does not pre-create it, so chown and USER caddy would otherwise fail.
RUN apk add --no-cache git git-daemon && \
  addgroup -g 1000 -S caddy && \
  adduser  -u 1000 -S caddy -G caddy


# Copy the compiled Caddy binary with plugins from builder stage
COPY --from=builder /usr/bin/caddy /usr/bin/caddy

# Init Git repository and set permissions
# This is necessary for git-http-backend to function properly
USER root

RUN mkdir -p /git-repos && \
    git init --bare /git-repos/nest.git

# Seed the bare repo from the nest source tree
# Build context is compose/, so 'nest/' maps to compose/nest/
COPY nest /srv/nest-src

# Seed the bare repo: point HEAD at main before cloning so the working tree
# starts on the correct branch, commit all nest content, push to the bare repo,
# then clean up scratch files.  Runs as root during build; the final chown
# restores caddy:caddy ownership so the runtime user can read the repo.
RUN git -C /git-repos/nest.git symbolic-ref HEAD refs/heads/main && \
  git clone /git-repos/nest.git /tmp/nest-seed && \
  cp -r /srv/nest-src/. /tmp/nest-seed/ && \
  git -C /tmp/nest-seed config user.email "seed@pg-stack.local" && \
  git -C /tmp/nest-seed config user.name "pg-stack seed" && \
  git -C /tmp/nest-seed add -A && \
  git -C /tmp/nest-seed commit -m "chore: initial seed" && \
  git -C /tmp/nest-seed push origin main && \
  rm -rf /tmp/nest-seed /srv/nest-src && \
  chown -R caddy:caddy /git-repos

# Copy Caddyfile for the caddy-cgi git server configuration
COPY nest-git/Caddyfile /etc/caddy/Caddyfile

# OCI image labels
LABEL pg-stack=true
LABEL org.opencontainers.image.title="pg-stack-nest-git"
LABEL org.opencontainers.image.description="Read-only HTTP Git server exposing a seeded nest.git repository via Caddy + git-http-backend."

# Drop to non-root for runtime; all root-privileged build steps are complete.
USER caddy

EXPOSE 8080
CMD ["caddy", "run", "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile"]
