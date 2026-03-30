// ─────────────────────────────────────────────────────────
//  Lucid AI — Makefile Generator
//  Company standard: gitlab.udevs.io:5050 registry.
// ─────────────────────────────────────────────────────────

/**
 * Generate a Makefile for the project.
 *
 * @param {object} opts
 * @param {string} opts.projectSlug — e.g. "crm-dashboard"
 * @param {string} opts.projectName — GitLab group/project path (default: derived from slug)
 * @returns {string}
 */
export function generateMakefile({
  projectSlug,
  projectName = '',
}) {
  const pn = projectName || projectSlug;

  return `CURRENT_DIR=$(shell pwd)

APP=$(shell basename \${CURRENT_DIR})

APP_CMD_DIR=\${CURRENT_DIR}/cmd

REGISTRY=gitlab.udevs.io:5050
TAG=latest
ENV_TAG=latest
PROJECT_NAME=${pn}
DOCKERFILE=Dockerfile

build-image:
\tdocker build --rm -t \${REGISTRY}/\${PROJECT_NAME}/\${APP}:\${TAG} . -f \${DOCKERFILE}
\tdocker tag \${REGISTRY}/\${PROJECT_NAME}/\${APP}:\${TAG} \${REGISTRY}/\${PROJECT_NAME}/\${APP}:\${ENV_TAG}

push-image:
\tdocker push \${REGISTRY}/\${PROJECT_NAME}/\${APP}:\${TAG}
\tdocker push \${REGISTRY}/\${PROJECT_NAME}/\${APP}:\${ENV_TAG}

clear-image:
\tdocker rmi \${REGISTRY}/\${PROJECT_NAME}/\${APP}:\${TAG}
\tdocker rmi \${REGISTRY}/\${PROJECT_NAME}/\${APP}:\${ENV_TAG}
`;
}
