// ─────────────────────────────────────────────────────────
//  Lucid AI — Integration Check
//  Checks availability of MCP-based integrations
//  (Supabase, Figma, etc.) before using them.
// ─────────────────────────────────────────────────────────

import fs from 'fs';
import path from 'path';

const MCP_CONFIG_PATHS = [
  '.mcp/config.json',
  'mcp_config.json',
  '.mcp.json',
];

/**
 * Locate the project root by walking up from cwd.
 */
function findProjectRoot() {
  let dir = process.cwd();
  if (path.basename(dir) === 'frontend') dir = path.dirname(dir);
  if (fs.existsSync(path.join(dir, '.git')) || fs.existsSync(path.join(dir, '.env'))) return dir;
  const parent = path.dirname(dir);
  if (fs.existsSync(path.join(parent, '.git')) || fs.existsSync(path.join(parent, '.env'))) return parent;
  return dir;
}

/**
 * Load MCP config from the project root.
 * Returns the full config object or null.
 */
function loadMcpConfig() {
  const root = findProjectRoot();
  for (const rel of MCP_CONFIG_PATHS) {
    const full = path.join(root, rel);
    if (fs.existsSync(full)) {
      try {
        return JSON.parse(fs.readFileSync(full, 'utf-8'));
      } catch { /* skip malformed */ }
    }
  }
  return null;
}

// ═══════════════════════════════════════════════════════════
//  Check individual integrations
// ═══════════════════════════════════════════════════════════

/**
 * Check if Supabase MCP server is configured.
 * Returns:
 *   { available: true, serverConfig, hasToken }
 *   { available: false, reason }
 */
export function checkSupabaseMcp() {
  const config = loadMcpConfig();
  if (!config?.mcpServers?.supabase) {
    return {
      available: false,
      reason: 'Supabase MCP server not found in MCP config',
    };
  }

  const serverConfig = config.mcpServers.supabase;
  const token = process.env.SUPABASE_ACCESS_TOKEN || process.env.SUPABASE_MANAGEMENT_API_KEY;

  return {
    available: true,
    serverConfig,
    hasToken: !!token?.trim(),
  };
}

/**
 * Check if Figma MCP server is configured.
 */
export function checkFigmaMcp() {
  const config = loadMcpConfig();
  if (!config?.mcpServers?.figma) {
    return {
      available: false,
      reason: 'Figma MCP server not found in MCP config',
    };
  }

  const token = process.env.FIGMA_ACCESS_TOKEN;
  return {
    available: true,
    serverConfig: config.mcpServers.figma,
    hasToken: !!token?.trim(),
  };
}

/**
 * Run all integration checks.
 * Returns an object with the status of each integration.
 */
export function checkAllIntegrations() {
  return {
    supabase: checkSupabaseMcp(),
    figma: checkFigmaMcp(),
  };
}
