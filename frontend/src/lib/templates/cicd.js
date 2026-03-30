// ─────────────────────────────────────────────────────────
//  Lucid AI — GitLab CI/CD Template Generator
//  Uses company shared CI/CD includes (ucode/ops).
// ─────────────────────────────────────────────────────────

/**
 * Generate a .gitlab-ci.yml using shared ucode/ops CI/CD.
 * @returns {string}
 */
export function generateGitlabCI() {
  return `include:
  - project: 'ucode/ops/ci-cd'
    ref: master
    file: 'frontend/ci.gitlab-ci.yml'

  - project: 'ucode/ops/ci-cd'
    ref: master
    file: 'frontend/cd.gitlab-ci.yml'

stages:
  - build
  - deploy
`;
}
