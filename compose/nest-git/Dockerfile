# syntax=docker/dockerfile:1.7
# -----------------------------------------------------------------------------
# pg-stack/nest-git
#
# A tiny Alpine image that serves a single bare Git repository (`nest.git`)
# over HTTP on port 8080 via `lighttpd` + `git-http-backend` (CGI). The sample
# Nest content under `../nest/` is baked into the image at build time so the
# container has no runtime volume dependency.
#
# Build context contract
# ----------------------
# The build context MUST be the parent `compose/` directory so that
# `COPY nest /srv/nest-src` resolves. Task 4.2 wires this into
# `docker-compose.yml` with:
#
#     services:
#       nest-git:
#         build:
#           context: .                           # <- the `compose/` dir
#           dockerfile: ./nest-git/Dockerfile
#
# Building directly from this directory (`docker build ./nest-git`) will fail
# at the `COPY nest /srv/nest-src` step — that is intentional.
# -----------------------------------------------------------------------------
FROM alpine:3.20

# Image labels. `pg-stack=true` is the selector used by the `compose-clean`
# Makefile target (task 11.3) to prune only stack-owned images.
LABEL pg-stack="true" \
      org.opencontainers.image.title="pg-stack/nest-git" \
      org.opencontainers.image.description="HTTP Git source for the Polar-Gosling Cloud_Stack sample Nest repository" \
      org.opencontainers.image.source="https://github.com/PolarGoslingDev/Polar-Gosling-Backends"

# -----------------------------------------------------------------------------
# Runtime packages
# -----------------------------------------------------------------------------
#   git, git-daemon   provide `git-http-backend` at /usr/libexec/git-core/
#   lighttpd          HTTP server with mod_cgi / mod_alias / mod_setenv
#   curl              used by the compose healthcheck (task 4.2)
#   tini              minimal PID1 reaper for the `lighttpd -D` foreground
# -----------------------------------------------------------------------------
# hadolint ignore=DL3018
RUN apk add --no-cache \
        git=2.45.4-r0 \
        git-daemon=2.45.4-r0 \
        lighttpd=1.4.82-r0 \
        curl=8.14.1-r2 \
        tini=0.19.0-r3

# -----------------------------------------------------------------------------
# Unprivileged user that will own the bare repo and run lighttpd
# -----------------------------------------------------------------------------
RUN addgroup -S -g 1000 git \
 && adduser  -S -D -H -u 1000 -G git -s /sbin/nologin git

# Directories: bare-repo home, empty document-root, lighttpd state dirs.
RUN mkdir -p /srv/git /srv/www /var/log/lighttpd /var/cache/lighttpd \
 && chown -R git:git /srv/git /srv/www /var/log/lighttpd /var/cache/lighttpd

# -----------------------------------------------------------------------------
# Seed the bare repo at build time
#
# Steps (all executed as root, chowned to `git` at the end):
#   1. Copy sample Nest content from the build context into /srv/nest-src.
#   2. `git init --bare /srv/git/nest.git` on default branch `main`.
#   3. Build a throw-away working tree in /tmp/work, seed it, commit, and
#      push to the bare repo.
#   4. Run `git update-server-info` inside the bare repo so that dumb-HTTP
#      clients have a working fallback (smart HTTP does not require it, but
#      this costs us nothing and hardens the image).
#   5. Clean up scratch directories.
# -----------------------------------------------------------------------------
COPY nest /srv/nest-src

# hadolint ignore=DL3003
RUN set -eux; \
    git config --global user.email 'seed@pg-stack.local'; \
    git config --global user.name  'pg-stack-seed'; \
    git config --global init.defaultBranch main; \
    git init --bare /srv/git/nest.git; \
    mkdir -p /tmp/work; \
    cd /tmp/work; \
    git init -b main; \
    cp -a /srv/nest-src/. /tmp/work/; \
    git add -A; \
    git commit -m 'seed'; \
    git remote add origin /srv/git/nest.git; \
    git push origin main; \
    git -C /srv/git/nest.git update-server-info; \
    rm -rf /tmp/work /srv/nest-src; \
    chown -R git:git /srv/git

# -----------------------------------------------------------------------------
# lighttpd configuration
#
# Minimal config that routes `/nest.git/*` through `git-http-backend` as CGI
# and injects the two environment variables the backend requires. Error log
# streams to the container's stderr; PID file goes to /tmp so the unprivileged
# `git` user can write it.
#
# The alias maps the URL prefix `/nest.git` to the backend binary. `cgi.assign`
# with empty extension + empty interpreter tells lighttpd to execute the
# aliased target directly as CGI. `$HTTP["url"]` scopes the CGI handler to
# the nest-git URL space so other (future) paths are unaffected.
# -----------------------------------------------------------------------------
# hadolint ignore=SC1088
RUN <<'CONF' cat > /etc/lighttpd/lighttpd.conf
server.modules = (
    "mod_alias",
    "mod_cgi",
    "mod_setenv",
)

server.document-root = "/srv/www"
server.port          = 8080
server.errorlog      = "/dev/stderr"
server.pid-file      = "/tmp/lighttpd.pid"

# Map the /nest.git URL prefix onto the git-http-backend CGI binary.
alias.url = (
    "/nest.git" => "/usr/libexec/git-core/git-http-backend",
)

$HTTP["url"] =~ "^/nest\.git" {
    cgi.assign = ( "" => "" )
    setenv.add-environment = (
        "GIT_PROJECT_ROOT"    => "/srv/git",
        "GIT_HTTP_EXPORT_ALL" => "1",
    )
}
CONF

# -----------------------------------------------------------------------------
# Runtime
# -----------------------------------------------------------------------------
USER git
EXPOSE 8080

# tini reaps any short-lived CGI children spawned by lighttpd; `-D` keeps
# lighttpd in the foreground so Docker can supervise it directly.
ENTRYPOINT ["/sbin/tini", "--"]
CMD ["lighttpd", "-D", "-f", "/etc/lighttpd/lighttpd.conf"]
