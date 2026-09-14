# Dokku deployment and HTTPS — DeltaTrack

The public FastAPI app is deployed as a prebuilt Railpack OCI image. GitHub Actions
builds the image, pushes it to GHCR, and asks Dokku to deploy the Git-generated
unambiguous short-SHA tag. Dokku's nginx proxy terminates TLS and forwards both the
static site and `/api/compare` to the same single Uvicorn process.

## One-time GitHub configuration

Configure two repository secrets:

- `GIT_REMOTE_URL`: the Dokku app's SSH URL, including the host, SSH user, port,
  and static app name. For example:
  `ssh://dokku@dokku.example.org:22/delta-track`.
- `SSH_PRIVATE_KEY`: the private key matching a public key authorized on the Dokku
  host.

The workflow uses the built-in `GITHUB_TOKEN` for GHCR and grants it only
`contents: read` and `packages: write`; it is not a repository secret.

## One-time Dokku setup

Ask the host maintainer whether these steps are already complete before changing the
host.

1. Create the stateless app and keep it at one web process:

   ```bash
   dokku apps:create delta-track
   dokku ps:scale delta-track web=1
   ```

   Do not scale past one process. The slowapi counters in `web/app.py` use in-memory,
   process-local storage, so multiple processes multiply the effective per-client
   request allowance.

2. Make the GHCR image pullable. Public packages need no registry credential. If
   the GHCR image is private, provision a read-only GHCR token on
   the host once; do not add it as another GitHub repository secret:

   ```bash
   dokku registry:login delta-track ghcr.io GITHUB_USERNAME GHCR_READ_TOKEN
   ```

   Dokku stores per-app registry credentials under its own configuration. See
   [Dokku registry management](https://dokku.com/docs/advanced-usage/registry-management/).

3. Point the app domain at the host:

   ```bash
   dokku domains:set delta-track deltatrack.agoradmv.org
   ```

4. After the first successful HTTP deployment, install/configure the official Let's
   Encrypt plugin if the host does not already have it:

   ```bash
   sudo dokku plugin:install https://github.com/dokku/dokku-letsencrypt.git
   sudo dokku letsencrypt:cron-job --add
   dokku letsencrypt:set delta-track email MAINTAINER_EMAIL
   dokku letsencrypt:enable delta-track
   ```

   The plugin's HTTP-01 flow requires the deployed app and DNS to be reachable before
   `letsencrypt:enable`. Dokku's default nginx TLS template redirects HTTP to HTTPS.
   See the [official plugin instructions](https://github.com/dokku/dokku-letsencrypt).

5. Size the host and container limit for PDF diffing. Each upload can be 150 MB and
   the app admits two concurrent diffs. Set the memory limit from observed peak PDF
   workloads, leaving operating-system and nginx headroom; do not copy a speculative
   fixed value from this runbook. No database plugin or persistent volume is needed.

6. Keep nginx's request-body limit at least `157286400` bytes and its proxy timeout
   above the app's 120-second diff timeout. These values must stay aligned with
   `MAX_UPLOAD_BYTES` and `DIFF_TIMEOUT_S` in `web/app.py`.

## Deployment behavior

`.github/workflows/deploy.yml` runs on pushes to `main`. It:

1. Sets `IMAGE_NAME` to `ghcr.io/${{ github.repository }}`, then builds and pushes it
   with `iloveitaly/github-action-railpack`. The action publishes the default `latest`
   tag and Git's unambiguous short-SHA tag with `GITHUB_TOKEN`.
2. Calls `dokku/github-action` with `GIT_REMOTE_URL`, `SSH_PRIVATE_KEY`, and the
   short-SHA image. The action connects to the URL's app and runs the equivalent of:

   ```bash
   dokku git:from-image delta-track ghcr.io/<owner>/<repository>:SHORT_SHA
   ```

The Procfile binds Uvicorn to `0.0.0.0` and `${PORT:-5000}`. It intentionally has no
`--workers` argument, so Uvicorn runs one worker.

## Verify proxy behavior

Dokku's nginx must preserve `Host`, set `X-Forwarded-Proto` to the original client
scheme, and ensure the rightmost `X-Forwarded-For` address is proxy-controlled. The
application uses those signals for HTTPS redirects and rate-limit identity.

After setup or a proxy-template change, verify both schemes:

```bash
curl -sI http://deltatrack.agoradmv.org/index.html | head -5
curl -sI https://deltatrack.agoradmv.org/ | head -5
```

The HTTP request should redirect once to HTTPS; the HTTPS request should return the
landing page without a redirect loop. If HTTPS loops, inspect the nginx
`X-Forwarded-Proto` value before changing application middleware.
