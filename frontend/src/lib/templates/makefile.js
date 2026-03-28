// ─────────────────────────────────────────────────────────
//  Lucid AI — Makefile Generator
//  Standard targets for build, test, Docker, and deploy.
// ─────────────────────────────────────────────────────────

/**
 * Generate a Makefile for the project.
 *
 * @param {object} opts
 * @param {string} opts.projectSlug
 * @param {string} opts.stack — 'nextjs'|'react'|'vue'|'angular'
 * @param {string} opts.registryUrl — Docker registry (default: registry.gitlab.udevs.io)
 * @param {string} opts.namespace — K8s namespace (default: frontend-prod)
 * @returns {string}
 */
export function generateMakefile({
  projectSlug,
  stack = 'nextjs',
  registryUrl = 'registry.gitlab.udevs.io',
  namespace = 'frontend-prod',
}) {
  const port = stack === 'nextjs' ? 3000 : 80;
  const imageName = `${registryUrl}/${projectSlug}`;

  return `# ─────────────────────────────────────────────────
#  ${projectSlug} — Makefile
# ─────────────────────────────────────────────────

APP_NAME    := ${projectSlug}
REGISTRY    := ${registryUrl}
IMAGE       := $(REGISTRY)/$(APP_NAME)
TAG         := $(shell git rev-parse --short HEAD 2>/dev/null || echo "latest")
PORT        := ${port}
NAMESPACE   := ${namespace}

.PHONY: install build dev test lint clean docker-build docker-push docker-run deploy

# ── Development ──────────────────────────────────

install:
\tnpm install

build:
\tnpm run build

dev:
\tnpm run dev

test:
\tnpm test || true

lint:
\tnpm run lint || true

clean:
\trm -rf node_modules .next dist out

# ── Docker ───────────────────────────────────────

docker-build:
\tdocker build -t $(IMAGE):$(TAG) -t $(IMAGE):latest .

docker-push: docker-build
\tdocker push $(IMAGE):$(TAG)
\tdocker push $(IMAGE):latest

docker-run:
\tdocker run --rm -p $(PORT):$(PORT) $(IMAGE):latest

# ── Deploy ───────────────────────────────────────

deploy:
\t@echo "Deploying $(APP_NAME) to $(NAMESPACE)..."
\tkubectl set image deployment/$(APP_NAME) $(APP_NAME)=$(IMAGE):$(TAG) -n $(NAMESPACE)
\t@echo "✓ Deployed $(TAG)"

rollback:
\tkubectl rollout undo deployment/$(APP_NAME) -n $(NAMESPACE)
\t@echo "✓ Rolled back $(APP_NAME)"

logs:
\tkubectl logs -f deployment/$(APP_NAME) -n $(NAMESPACE) --tail=100

status:
\tkubectl get pods -n $(NAMESPACE) -l app=$(APP_NAME)
`;
}
