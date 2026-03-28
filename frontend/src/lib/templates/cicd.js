// ─────────────────────────────────────────────────────────
//  Lucid AI — GitLab CI/CD Template Generator
//  Generates .gitlab-ci.yml using company shared CI/CD.
// ─────────────────────────────────────────────────────────

/**
 * Generate a .gitlab-ci.yml file using company shared CI/CD includes.
 *
 * @returns {string}
 */
export function generateGitlabCI() {
  return `include:
  - project: 'ucode/ops/ci-cd'
    ref: master
    file: 'vault/vault.gitlab-ci.yml'

  - project: 'ucode/ops/ci-cd'
    ref: master
    file: 'frontend/ci.gitlab-ci.yml'

  - project: 'ucode/ops/ci-cd'
    ref: master
    file: 'frontend/cd.gitlab-ci.yml'

stages:
  - vault
  - build
  - deploy
`;
}
