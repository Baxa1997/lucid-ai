"""
CI/CD Service — Full pipeline setup for Lucid AI generated projects.

Handles:
  1. Generating CI/CD infrastructure files (Dockerfile, .gitlab-ci.yml, nginx.conf)
  2. Creating Helm values.yaml in the ops repository
  3. Adding GitLab CI/CD variables via API
  4. Committing and pushing all changes

Company standard:
  - GitLab at gitlab.udevs.io
  - Kubernetes deployment via Helm
  - nginx-based frontends (React/Vite)
  - Node.js server for Next.js
"""

import os
import logging
import subprocess
from typing import Optional

logger = logging.getLogger("lucid.cicd")


# ═══════════════════════════════════════════════════════════
#  1. .gitlab-ci.yml Generator
# ═══════════════════════════════════════════════════════════

def generate_gitlab_ci(stack: str) -> str:
    """Generate .gitlab-ci.yml content based on project stack.

    Uses Docker-in-Docker for builds and kubectl for deploys.
    """
    stack = (stack or "react-vite").lower().strip()

    return """\
stages:
  - build
  - deploy

variables:
  IMAGE_TAG: $CI_COMMIT_SHORT_SHA

build:
  stage: build
  image: docker:24
  services:
    - docker:24-dind
  script:
    - docker build -t $CI_REGISTRY_IMAGE:$IMAGE_TAG .
    - docker login -u $CI_REGISTRY_USER -p $CI_REGISTRY_PASSWORD $CI_REGISTRY
    - docker push $CI_REGISTRY_IMAGE:$IMAGE_TAG
  only:
    - main

deploy:
  stage: deploy
  image: bitnami/kubectl:latest
  script:
    - kubectl set image deployment/$APP_NAME $APP_NAME=$CI_REGISTRY_IMAGE:$IMAGE_TAG -n $K8S_NAMESPACE_PROD
  only:
    - main
"""


# ═══════════════════════════════════════════════════════════
#  2. Dockerfile Generator
# ═══════════════════════════════════════════════════════════

def generate_dockerfile(stack: str) -> str:
    """Generate production Dockerfile based on project stack.

    Returns:
        Dockerfile content as string.

    Stack support:
        - nextjs:     Node.js server on port 3000
        - react-vite: Multi-stage build → nginx on port 80
        - html-css:   Static files → nginx on port 80
        - fastapi:    Python uvicorn on port 8000
        - nodejs:     Node.js on port 3000
        - nestjs:     Node.js on port 3000
        - django:     Python gunicorn on port 8000
    """
    stack = (stack or "react-vite").lower().strip()

    if stack == "nextjs":
        return """\
FROM node:20-alpine AS builder
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM node:20-alpine AS runner
WORKDIR /app
ENV NODE_ENV production
COPY --from=builder /app/.next ./.next
COPY --from=builder /app/node_modules ./node_modules
COPY --from=builder /app/package.json ./package.json
COPY --from=builder /app/public ./public
EXPOSE 3000
CMD ["npm", "start"]
"""

    if stack in ("react-vite", "react"):
        return """\
FROM node:20-alpine AS builder
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:alpine
COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
"""

    if stack == "html-css":
        return """\
FROM nginx:alpine
COPY . /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
"""

    if stack == "fastapi":
        return """\
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
"""

    if stack == "django":
        return """\
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8000
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3"]
"""

    if stack in ("nodejs", "node-express", "nestjs"):
        return """\
FROM node:20-alpine AS builder
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build || true

FROM node:20-alpine
WORKDIR /app
ENV NODE_ENV production
COPY --from=builder /app/dist ./dist
COPY --from=builder /app/node_modules ./node_modules
COPY --from=builder /app/package.json ./package.json
EXPOSE 3000
CMD ["node", "dist/main.js"]
"""

    # Default fallback: nginx-based
    return """\
FROM node:20-alpine AS builder
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:alpine
COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
"""


# ═══════════════════════════════════════════════════════════
#  3. nginx.conf Generator
# ═══════════════════════════════════════════════════════════

def generate_nginx_conf() -> str:
    """Generate nginx.conf for React+Vite and HTML/CSS projects."""
    return """\
server {
    listen 80;
    server_name _;
    root /usr/share/nginx/html;
    index index.html;

    location / {
        try_files $uri $uri/ /index.html;
    }

    location ~* \\.(js|css|png|jpg|jpeg|gif|ico|svg|woff|woff2|ttf|eot)$ {
        expires 1y;
        add_header Cache-Control "public, immutable";
    }

    gzip on;
    gzip_types
        text/plain
        text/css
        application/javascript
        application/json
        application/xml
        image/svg+xml;
}
"""


# ═══════════════════════════════════════════════════════════
#  4. values.yaml Generator (Helm / K8s)
# ═══════════════════════════════════════════════════════════

def _get_port(stack: str) -> int:
    """Get the exposed port based on stack."""
    stack = (stack or "").lower().strip()
    if stack in ("nextjs", "nodejs", "node-express", "nestjs"):
        return 3000
    if stack in ("fastapi", "django"):
        return 8000
    # react-vite, html-css, default → nginx
    return 80


def generate_values_yaml(
    project_name: str,
    stack: str,
    gitlab_group: str = "",
) -> str:
    """Generate Helm values.yaml for Kubernetes deployment.

    Args:
        project_name: Slugified project name (e.g. 'crm-dashboard')
        stack: Project stack type
        gitlab_group: GitLab group path for container registry
    """
    port = _get_port(stack)
    group = gitlab_group or "lucid-projects"
    registry = f"registry.gitlab.udevs.io/{group}/{project_name}"

    return f"""\
replicaCount: 1

image:
  repository: {registry}
  tag: latest
  pullPolicy: Always

service:
  type: ClusterIP
  port: {port}

ingress:
  enabled: true
  annotations:
    kubernetes.io/ingress.class: nginx
  hosts:
    - host: "{project_name}.udevs.io"
      paths:
        - path: /
          pathType: Prefix
          servicePort: {port}
  tls:
    - secretName: {project_name}-tls
      hosts:
        - "{project_name}.udevs.io"

resources:
  requests:
    memory: "128Mi"
    cpu: "100m"
  limits:
    memory: "512Mi"
    cpu: "500m"

labels:
  app: {project_name}

podLabels:
  app: {project_name}
"""


# ═══════════════════════════════════════════════════════════
#  5. GitLab Variable Helper
# ═══════════════════════════════════════════════════════════

async def add_gitlab_variable(
    gitlab_url: str,
    project_id: str,
    token: str,
    key: str,
    value: str,
) -> bool:
    """Add a single CI/CD variable to a GitLab project.

    Returns True on success (201) or if variable already exists (409).
    """
    import httpx

    url = f"{gitlab_url}/api/v4/projects/{project_id}/variables"
    headers = {"PRIVATE-TOKEN": token}
    payload = {
        "key": key,
        "value": value,
        "protected": False,
        "masked": False,
        "environment_scope": "*",
    }

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(url, headers=headers, json=payload)

            if resp.status_code == 201:
                logger.info("GitLab variable %s added to project %s", key, project_id)
                return True

            if resp.status_code == 409:
                # Already exists — update it instead
                update_url = f"{url}/{key}"
                resp2 = await client.put(
                    update_url,
                    headers=headers,
                    json={"value": value, "protected": False, "masked": False},
                )
                if resp2.status_code in (200, 201):
                    logger.info("GitLab variable %s updated in project %s", key, project_id)
                    return True
                logger.warning("GitLab variable %s update failed: %s", key, resp2.text[:200])
                return False

            logger.warning(
                "GitLab variable %s failed (%d): %s",
                key, resp.status_code, resp.text[:200],
            )
            return False

    except Exception as e:
        logger.error("GitLab variable %s API error: %s", key, e)
        return False


# ═══════════════════════════════════════════════════════════
#  6. Main Pipeline — setup_project_cicd
# ═══════════════════════════════════════════════════════════

async def setup_project_cicd(
    workspace_path: str,
    project_name: str,
    stack: str,
    gitlab_token: str,
    gitlab_project_id: str,
    ops_repo_path: str = "",
    gitlab_group: str = "",
    websocket=None,
) -> bool:
    """Full CI/CD pipeline setup for a generated project.

    Steps:
        A. Write CI/CD files to project workspace (Dockerfile, .gitlab-ci.yml, nginx.conf)
        B. Commit CI/CD files in the project repo
        C. Create ops folder in ops deployment repo
        D. Write values.yaml to ops folder
        E. Commit + push ops changes
        F. Add GitLab CI/CD variables (K8S_NAMESPACE_PROD, APP_NAME)
        G. Push project repo with CI/CD files

    All steps are wrapped in try/except. Failure in ops/variables does NOT
    block the project from being created. The function logs errors but continues.

    Returns True if at least Dockerfile + .gitlab-ci.yml were written.
    """
    gitlab_url = os.environ.get("GITLAB_URL", "https://gitlab.udevs.io")
    ops_path = ops_repo_path or os.environ.get("OPS_REPO_PATH", "")
    group = gitlab_group or os.environ.get("GITLAB_GROUP", "lucid-projects")
    success = False

    async def _ws_progress(msg: str):
        if websocket:
            try:
                await websocket.send_json({"type": "progress", "message": msg})
            except Exception:
                pass

    # ── Step A: Write CI/CD files to workspace ──────────────
    try:
        await _ws_progress("🔧 Adding CI/CD pipeline files...")

        # .gitlab-ci.yml
        ci_content = generate_gitlab_ci(stack)
        ci_path = os.path.join(workspace_path, ".gitlab-ci.yml")
        with open(ci_path, "w") as f:
            f.write(ci_content)
        logger.info("Wrote .gitlab-ci.yml to %s", ci_path)

        # Dockerfile
        dockerfile_content = generate_dockerfile(stack)
        dockerfile_path = os.path.join(workspace_path, "Dockerfile")
        with open(dockerfile_path, "w") as f:
            f.write(dockerfile_content)
        logger.info("Wrote Dockerfile to %s", dockerfile_path)

        # nginx.conf (for non-Node stacks)
        needs_nginx = stack in ("react-vite", "react", "html-css", "")
        if needs_nginx:
            nginx_content = generate_nginx_conf()
            nginx_path = os.path.join(workspace_path, "nginx.conf")
            with open(nginx_path, "w") as f:
                f.write(nginx_content)
            logger.info("Wrote nginx.conf to %s", nginx_path)

        success = True
        await _ws_progress("✅ CI/CD files created")
    except Exception as e:
        logger.error("Step A (CI/CD files) failed: %s", e)
        await _ws_progress(f"⚠️ CI/CD file generation failed: {e}")
        return False

    # ── Step B: Commit CI/CD files locally ──────────────────
    try:
        await _ws_progress("📝 Committing CI/CD files...")
        subprocess.run(
            ["git", "add", ".gitlab-ci.yml", "Dockerfile"]
            + (["nginx.conf"] if needs_nginx else []),
            cwd=workspace_path,
            capture_output=True, timeout=10,
        )
        result = subprocess.run(
            ["git", "commit", "-m", "🔧 Add CI/CD pipeline — Lucid AI"],
            cwd=workspace_path,
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0:
            logger.info("CI/CD files committed in project repo")
        else:
            logger.warning("CI/CD commit returned %d: %s", result.returncode, result.stderr[:200])
    except Exception as e:
        logger.error("Step B (commit CI/CD) failed: %s", e)
        # Non-fatal — files are on disk even if commit fails

    # ── Step C & D: Create ops folder + write values.yaml ──
    if ops_path and os.path.isdir(ops_path):
        try:
            await _ws_progress("📁 Creating ops configuration...")

            ops_folder = os.path.join(ops_path, project_name)
            os.makedirs(ops_folder, exist_ok=True)

            values_content = generate_values_yaml(project_name, stack, group)
            values_path = os.path.join(ops_folder, "values.yaml")
            with open(values_path, "w") as f:
                f.write(values_content)
            logger.info("Wrote values.yaml to %s", values_path)

            await _ws_progress("✅ Ops configuration created")
        except Exception as e:
            logger.error("Step C/D (ops folder) failed: %s", e)
            await _ws_progress(f"⚠️ Ops config failed: {e}")

        # ── Step E: Commit + push ops changes ──────────────
        try:
            await _ws_progress("📤 Pushing ops changes...")

            # Pull latest first
            subprocess.run(
                ["git", "pull", "--rebase", "origin", "main"],
                cwd=ops_path,
                capture_output=True, timeout=30,
            )

            subprocess.run(
                ["git", "add", f"{project_name}/"],
                cwd=ops_path,
                capture_output=True, timeout=10,
            )
            commit_result = subprocess.run(
                ["git", "commit", "-m", f"Add {project_name} deployment — Lucid AI"],
                cwd=ops_path,
                capture_output=True, text=True, timeout=15,
            )
            if commit_result.returncode == 0:
                push_result = subprocess.run(
                    ["git", "push", "origin", "main"],
                    cwd=ops_path,
                    capture_output=True, text=True, timeout=30,
                )
                if push_result.returncode == 0:
                    logger.info("Ops changes pushed for %s", project_name)
                    await _ws_progress("✅ Ops repo updated")
                else:
                    logger.warning("Ops push failed: %s", push_result.stderr[:200])
                    await _ws_progress("⚠️ Ops push failed — push manually")
            else:
                logger.info("Ops commit: nothing to commit (may already exist)")
        except Exception as e:
            logger.error("Step E (ops push) failed: %s", e)
            await _ws_progress(f"⚠️ Ops push failed: {e}")
    else:
        if ops_path:
            logger.warning("OPS_REPO_PATH '%s' does not exist — skipping ops setup", ops_path)
        else:
            logger.info("No OPS_REPO_PATH set — skipping ops setup")

    # ── Step F: Add GitLab CI/CD variables ─────────────────
    if gitlab_token and gitlab_project_id:
        try:
            await _ws_progress("🔑 Setting deployment variables...")

            vars_to_set = {
                "K8S_NAMESPACE_PROD": "frontend-prod",
                "APP_NAME": project_name,
            }

            all_ok = True
            for key, value in vars_to_set.items():
                ok = await add_gitlab_variable(
                    gitlab_url=gitlab_url,
                    project_id=gitlab_project_id,
                    token=gitlab_token,
                    key=key,
                    value=value,
                )
                if not ok:
                    all_ok = False
                    logger.warning("Failed to set GitLab variable: %s", key)

            if all_ok:
                await _ws_progress("✅ Deployment variables configured")
            else:
                await _ws_progress("⚠️ Some variables failed — check GitLab settings")

        except Exception as e:
            logger.error("Step F (GitLab variables) failed: %s", e)
            await _ws_progress(f"⚠️ Variable setup failed: {e}")
    else:
        logger.info("No GitLab token/project_id — skipping variable setup")

    # ── Step G: Push project with CI/CD files ──────────────
    try:
        await _ws_progress("🚀 Pushing project with CI/CD...")
        push_result = subprocess.run(
            ["git", "push", "origin", "main"],
            cwd=workspace_path,
            capture_output=True, text=True, timeout=60,
        )
        if push_result.returncode == 0:
            logger.info("Project pushed with CI/CD files")
            await _ws_progress("✅ CI/CD pipeline ready!")
        else:
            err = push_result.stderr or push_result.stdout or "unknown"
            # Mask tokens in error messages
            if gitlab_token:
                err = err.replace(gitlab_token, "***")
            logger.warning("Project push with CI/CD failed: %s", err[:300])
            await _ws_progress("⚠️ Push failed — CI/CD files saved locally")
    except Exception as e:
        logger.error("Step G (project push) failed: %s", e)
        await _ws_progress(f"⚠️ Push failed: {e}")

    return success
