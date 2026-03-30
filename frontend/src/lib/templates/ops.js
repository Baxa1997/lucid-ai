// ─────────────────────────────────────────────────────────
//  Lucid AI — K8s Ops Folder Generator
//  Creates config.json and values.yaml for Helm deployments.
//
//  Files are placed at:
//  clusters/cluster-prod/frontend-prod/{project_name}/
//    ├── config.json
//    └── values.yaml
//
//  Company standard: microservice_v2 chart, GitLab registry,
//  nginx ingress with CORS, node affinity for workload.
// ─────────────────────────────────────────────────────────

/**
 * Generate K8s ops folder files (config.json + values.yaml).
 *
 * @param {object} opts
 * @param {string} opts.projectSlug — e.g. "crm-dashboard"
 * @param {string} opts.repoPath — GitLab repo path, e.g. "ucode/lodify/crm-dashboard"
 * @param {string} opts.registryUrl — Docker registry (default: gitlab.udevs.io:5050)
 * @param {string} opts.domain — ingress domain, e.g. "crm-dashboard.udevs.io"
 * @param {number} opts.servicePort — from Dockerfile EXPOSE (default 80)
 * @param {string} opts.appLabel — label for K8s resources (default: derived from slug)
 * @returns {{ folderName: string, files: { path: string, content: string }[] }}
 */
export function generateOpsFolder({
  projectSlug,
  repoPath,
  registryUrl = 'gitlab.udevs.io:5050',
  domain = '',
  servicePort = 80,
  appLabel,
}) {
  const label = appLabel || projectSlug.replace(/_/g, '-');
  const imageRepo = repoPath || projectSlug;
  const tlsSecret = `${label}-tls`;
  const hostDomain = domain || `${projectSlug}.udevs.io`;

  // Path inside the ops deployments repo
  const basePath = `clusters/cluster-prod/frontend-prod/${projectSlug}`;

  const configJson = JSON.stringify(
    {
      repo_url:
        'https://gitlab.udevs.io/api/v4/projects/476/packages/helm/stable',
      chart: 'microservice_v2',
      version: '0.6.2',
    },
    null,
    4
  );

  const valuesYaml = `# Default values for microservice.
# This is a YAML-formatted file.
# Declare variables to be passed into your templates.
global:
  replicaCount: 1
  environment: {}
  # list of key: value
  # GLOBAL1: value
replicaCount: 1
image:
  registry: ${registryUrl}/
  repository: ${imageRepo}
  pullPolicy: IfNotPresent
  # Overrides the image tag whose default is the chart appVersion.
  tag: "latest"
imagePullSecrets: "gitlab-registry"
nameOverride: ""
fullnameOverride: ""
labels:
  app: ${label}
# command: ["/bin/sh","-c"]
# args: ["echo 'consuming a message'; sleep 5"]

serviceAccount:
  # Specifies whether a service account should be created
  create: true
  # Annotations to add to the service account
  annotations: {}
  # The name of the service account to use.
  # If not set and create is true, a name is generated using the fullname template
  name: "vault-auth"
podLabels:
  app: ${label}
podSecurityContext: {}
# fsGroup: 2000

securityContext: {}
# capabilities:
#   drop:
#     - ALL
# readOnlyRootFilesystem: true
# runAsNonRoot: true
# runAsUser: 1000
vault:
  enabled: false
  secretName: vault-secrets
  variables:
    VAULT_TOKEN: vault_token
    SECRETS_PATH:
environment: {}
volumes:
  enabled: false
  pvc:
    enabled: false
    existing_claim:
    name: pvc
    mountPath: /pv
    size: 1G
    class:
    accessModes:
      - ReadWriteOnce
service:
  type: ClusterIP
  annotations: {}
  specs:
    - port: ${servicePort}
      targetPort: ${servicePort}
      name: https
ingress:
  enabled: true
  rules:
    - annotations:
        nginx.ingress.kubernetes.io/enable-cors: "true"
        nginx.ingress.kubernetes.io/cors-allow-origin: "*"
        nginx.ingress.kubernetes.io/cors-allow-methods: "PUT, GET, POST, OPTIONS, DELETE"
        kubernetes.io/ingress.class: nginx
        # acme.cert-manager.io/http01-edit-in-place: "true"
        cert-manager.io/cluster-issuer: letsencrypt-prod
      type: web
      hosts:
        - host: "${hostDomain}"
          path: /
          servicePort: ${servicePort}
      tls:
        - secretName: ${tlsSecret}
          hosts:
            - "${hostDomain}"
resources: {}
#  limits:
#    cpu: 150m
#    memory: 256Mi
#  requests:
#    cpu: 100m
#    memory: 200Mi

autoscaling:
  enabled: false
  minReplicas: 1
  maxReplicas: 100
  targetCPUUtilizationPercentage: 80
  # targetMemoryUtilizationPercentage: 80
nodeSelector: {}
tolerations: []
affinity:
  nodeAffinity:
    requiredDuringSchedulingIgnoredDuringExecution:
      nodeSelectorTerms:
        - matchExpressions:
            - key: workload
              operator: In
              values:
                - "ucode-frontend"
`;

  return {
    folderName: projectSlug,
    files: [
      {
        path: `${basePath}/config.json`,
        content: configJson,
      },
      {
        path: `${basePath}/values.yaml`,
        content: valuesYaml,
      },
    ],
  };
}
