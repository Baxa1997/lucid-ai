// ─────────────────────────────────────────────────────────
//  Lucid AI — Figma Design Token Extraction
//  5-step pipeline:
//    1. Check for Figma MCP config
//    2. Ensure MCP is configured (or log action required)
//    3. Extract design tokens via Figma MCP
//    4. Fallback to Figma REST API if MCP fails
//    5. Return formatted tokens for prompt injection
//
//  NOTE: MCP SDK is optional — imported dynamically only
//  when an MCP config is found. Build works without it.
// ─────────────────────────────────────────────────────────

import fs from 'fs';
import path from 'path';

// ── MCP config locations to search (project root) ───────
const MCP_CONFIG_PATHS = [
  '.mcp/config.json',
  'mcp_config.json',
  '.mcp.json',
];

const FIGMA_REST_API = 'https://api.figma.com/v1';

// ═══════════════════════════════════════════════════════════
//  Step 1 — Check for Figma MCP config
// ═══════════════════════════════════════════════════════════

/**
 * Search for MCP config and check if Figma server is registered.
 * Returns { found, configPath, serverConfig } or { found: false }.
 */
export function checkFigmaMcpConfig() {
  // Walk up from frontend/src/lib to find project root
  const projectRoot = findProjectRoot();
  if (!projectRoot) {
    console.warn('[figma] Could not determine project root');
    return { found: false };
  }

  for (const rel of MCP_CONFIG_PATHS) {
    const fullPath = path.join(projectRoot, rel);
    if (fs.existsSync(fullPath)) {
      try {
        const config = JSON.parse(fs.readFileSync(fullPath, 'utf-8'));
        const figmaServer = config.mcpServers?.figma;
        if (figmaServer) {
          console.log(`[figma] ✓ Figma MCP found in ${rel}`);
          return {
            found: true,
            configPath: fullPath,
            serverConfig: figmaServer,
          };
        }
      } catch (err) {
        console.warn(`[figma] Failed to parse ${rel}:`, err.message);
      }
    }
  }

  console.warn('[figma] Figma MCP not found in any config file');
  return { found: false };
}

/**
 * Find the project root by walking up from this file.
 */
function findProjectRoot() {
  // This file is at: <root>/frontend/src/lib/figma.js
  // So project root is 3 levels up from __dirname
  // But in Next.js, __dirname may not be reliable — use process.cwd()
  let dir = process.cwd();

  // If we're in frontend/, go up one level
  if (path.basename(dir) === 'frontend') {
    dir = path.dirname(dir);
  }

  // Verify by checking for .git or .env
  if (fs.existsSync(path.join(dir, '.git')) || fs.existsSync(path.join(dir, '.env'))) {
    return dir;
  }

  // Try parent
  const parent = path.dirname(dir);
  if (fs.existsSync(path.join(parent, '.git')) || fs.existsSync(path.join(parent, '.env'))) {
    return parent;
  }

  return dir;
}

// ═══════════════════════════════════════════════════════════
//  Step 2 — Verify token is available
// ═══════════════════════════════════════════════════════════

/**
 * Check if FIGMA_ACCESS_TOKEN is set and non-empty.
 */
function hasValidToken() {
  const token = process.env.FIGMA_ACCESS_TOKEN;
  if (!token || !token.trim()) {
    console.warn(
      '[figma] ⚠ ACTION REQUIRED: Add your FIGMA_ACCESS_TOKEN to .env to enable Figma integration. ' +
      'Get it from: https://www.figma.com/settings > Personal access tokens.'
    );
    return false;
  }
  return true;
}

// ═══════════════════════════════════════════════════════════
//  Step 3 — Extract via Figma MCP
// ═══════════════════════════════════════════════════════════

/**
 * Connect to Figma MCP server and extract design data.
 * Returns parsed file data or null on failure.
 */
async function extractViaMcp(fileKey, nodeId, mcpConfig) {
  let client = null;
  let transport = null;

  try {
    // Dynamic import — MCP SDK is optional
    let Client, StdioClientTransport;
    try {
      const clientModule = await import('@modelcontextprotocol/sdk/client/index.js');
      const stdioModule = await import('@modelcontextprotocol/sdk/client/stdio.js');
      Client = clientModule.Client;
      StdioClientTransport = stdioModule.StdioClientTransport;
    } catch {
      console.warn('[figma] MCP SDK not installed — skipping MCP extraction');
      return null;
    }

    const token = process.env.FIGMA_ACCESS_TOKEN;

    // Resolve the MCP server command
    const command = mcpConfig?.command || 'figma-mcp-server';
    const args = mcpConfig?.args || [];

    // Build env — merge config env with process env, resolve ${VAR} placeholders
    const serverEnv = { ...process.env };
    if (mcpConfig?.env) {
      for (const [key, val] of Object.entries(mcpConfig.env)) {
        serverEnv[key] = val.replace(/\$\{(\w+)\}/g, (_, v) => process.env[v] || '');
      }
    }
    // Always ensure the token is available
    if (!serverEnv.FIGMA_ACCESS_TOKEN) {
      serverEnv.FIGMA_ACCESS_TOKEN = token;
    }

    transport = new StdioClientTransport({
      command,
      args: args.length > 0 ? args : ['--stdio'],
      env: serverEnv,
    });

    client = new Client(
      { name: 'lucid-ai', version: '1.0.0' },
      { capabilities: {} }
    );

    await client.connect(transport);
    console.log('[figma] ✓ Connected to Figma MCP server');

    // Discover tools
    const toolsList = await client.listTools();
    const toolNames = (toolsList.tools || []).map((t) => t.name);
    console.log('[figma] Available MCP tools:', toolNames.join(', '));

    // Call the appropriate tool to get file data
    const figmaArgs = { file_key: fileKey };
    if (nodeId) figmaArgs.node_id = nodeId;

    let result = null;

    // Try tool names in order of preference
    const toolPriority = ['get_figma_data', 'get_file', 'read_file', 'figma_get_file'];
    for (const toolName of toolPriority) {
      if (toolNames.includes(toolName)) {
        console.log(`[figma] Calling MCP tool: ${toolName}`);
        result = await client.callTool({
          name: toolName,
          arguments: figmaArgs,
        });
        break;
      }
    }

    if (!result) {
      console.warn('[figma] No compatible file-reading tool found in MCP server');
      return null;
    }

    const parsed = parseToolResult(result);
    if (parsed) {
      console.log('[figma] ✓ Using Figma MCP — data extracted successfully');
    }
    return parsed;
  } catch (err) {
    console.error('[figma] MCP extraction failed:', err.message);
    return null;
  } finally {
    try { if (client) await client.close(); } catch { /* ignore */ }
    try { if (transport) await transport.close(); } catch { /* ignore */ }
  }
}

/**
 * Parse MCP tool result into usable JSON.
 */
function parseToolResult(result) {
  if (!result) return null;
  const content = result.content || [];
  for (const block of content) {
    if (block.type === 'text' && block.text) {
      try { return JSON.parse(block.text); } catch { return { rawText: block.text }; }
    }
    if (block.type === 'resource' && block.resource?.text) {
      try { return JSON.parse(block.resource.text); } catch { return { rawText: block.resource.text }; }
    }
  }
  return null;
}

// ═══════════════════════════════════════════════════════════
//  Step 4 — Fallback to Figma REST API
// ═══════════════════════════════════════════════════════════

/**
 * Extract file data directly via Figma REST API.
 * Used as fallback when MCP is unavailable or fails.
 */
async function extractViaRestApi(fileKey, nodeId) {
  const token = process.env.FIGMA_ACCESS_TOKEN;
  if (!token?.trim()) return null;

  try {
    const url = nodeId
      ? `${FIGMA_REST_API}/files/${fileKey}/nodes?ids=${encodeURIComponent(nodeId)}&depth=2`
      : `${FIGMA_REST_API}/files/${fileKey}?depth=2`;

    const response = await fetch(url, {
      headers: { 'X-Figma-Token': token },
      signal: AbortSignal.timeout(15000),
    });

    if (!response.ok) {
      console.error('[figma] REST API error:', response.status, await response.text());
      return null;
    }

    const data = await response.json();
    console.log('[figma] ✓ Using Figma REST API fallback — data extracted successfully');
    return data;
  } catch (err) {
    console.error('[figma] REST API fallback failed:', err.message);
    return null;
  }
}

// ═══════════════════════════════════════════════════════════
//  Main entry point — orchestrates the full pipeline
// ═══════════════════════════════════════════════════════════

/**
 * Parse a Figma URL to extract fileKey and optional nodeId.
 */
export function parseFigmaUrl(url) {
  try {
    const u = new URL(url);
    const parts = u.pathname.split('/').filter(Boolean);
    const typeIdx = parts.findIndex((p) => p === 'file' || p === 'design');
    if (typeIdx === -1 || !parts[typeIdx + 1]) return null;
    return {
      fileKey: parts[typeIdx + 1],
      nodeId: u.searchParams.get('node-id') || null,
    };
  } catch {
    return null;
  }
}

/**
 * Extract design tokens from a Figma file.
 *
 * Pipeline:
 *   1. Check MCP config → 2. Verify token →
 *   3. Try MCP → 4. Fallback to REST API →
 *   5. Parse into { colors, typography, spacing, components, layouts }
 *
 * Returns null on any failure (caller should fall back silently).
 */
export async function extractDesignTokens(figmaUrl) {
  const parsed = parseFigmaUrl(figmaUrl);
  if (!parsed) {
    console.warn('[figma] Could not parse Figma URL:', figmaUrl);
    return null;
  }

  const { fileKey, nodeId } = parsed;

  // Step 1+2: Check config & token
  if (!hasValidToken()) return null;

  const mcpCheck = checkFigmaMcpConfig();
  let fileData = null;

  // Step 3: Try MCP first
  if (mcpCheck.found) {
    fileData = await extractViaMcp(fileKey, nodeId, mcpCheck.serverConfig);
  } else {
    console.log('[figma] MCP config not found — skipping to REST API');
  }

  // Step 4: Fallback to REST API
  if (!fileData) {
    console.log('[figma] Attempting REST API fallback…');
    fileData = await extractViaRestApi(fileKey, nodeId);
  }

  if (!fileData) {
    console.warn('[figma] All extraction methods failed');
    return null;
  }

  // Step 5: Parse into design tokens
  const tokens = {
    colors: extractColors(fileData),
    typography: extractTypography(fileData),
    spacing: extractSpacing(fileData),
    components: extractComponents(fileData),
    layouts: extractLayouts(fileData),
  };

  console.log('[figma] ✓ Design tokens ready:', {
    colors: tokens.colors.length,
    typography: tokens.typography.length,
    spacing: tokens.spacing.length,
    components: tokens.components.length,
    layouts: tokens.layouts.length,
  });

  return tokens;
}

// ═══════════════════════════════════════════════════════════
//  Token extraction helpers
// ═══════════════════════════════════════════════════════════

function walkNodes(node, fn) {
  if (!node) return;
  fn(node);
  if (node.children) {
    for (const child of node.children) {
      walkNodes(child, fn);
    }
  }
}

function rgbaToHex({ r, g, b, a }) {
  const toHex = (v) => Math.round((v || 0) * 255).toString(16).padStart(2, '0');
  const hex = `#${toHex(r)}${toHex(g)}${toHex(b)}`;
  if (a !== undefined && a < 1) return `${hex}${toHex(a)}`;
  return hex;
}

/** Extract color palette from all solid fills. */
function extractColors(data) {
  const colorMap = new Map();
  const doc = data.document || data;

  walkNodes(doc, (node) => {
    for (const fill of (node.fills || [])) {
      if (fill.type === 'SOLID' && fill.color && fill.visible !== false) {
        const hex = rgbaToHex(fill.color);
        colorMap.set(hex, (colorMap.get(hex) || 0) + 1);
      }
    }
  });

  return Array.from(colorMap.entries())
    .sort((a, b) => b[1] - a[1])
    .slice(0, 20)
    .map(([hex]) => hex);
}

/** Extract typography: font families, sizes, weights, line heights. */
function extractTypography(data) {
  const fontMap = new Map();
  const doc = data.document || data;

  walkNodes(doc, (node) => {
    const s = node.style;
    if (!s?.fontFamily) return;

    const family = s.fontFamily;
    if (!fontMap.has(family)) {
      fontMap.set(family, { sizes: new Set(), weights: new Set(), lineHeights: new Set() });
    }
    const entry = fontMap.get(family);
    if (s.fontSize) entry.sizes.add(s.fontSize);
    if (s.fontWeight) entry.weights.add(s.fontWeight);
    if (s.lineHeightPx) entry.lineHeights.add(Math.round(s.lineHeightPx));
  });

  return Array.from(fontMap.entries()).map(([family, data]) => ({
    family,
    sizes: Array.from(data.sizes).sort((a, b) => a - b),
    weights: Array.from(data.weights).sort((a, b) => a - b),
    lineHeights: Array.from(data.lineHeights).sort((a, b) => a - b),
  }));
}

/** Extract spacing values from auto-layout nodes. */
function extractSpacing(data) {
  const values = new Set();
  const doc = data.document || data;

  walkNodes(doc, (node) => {
    if (node.paddingLeft != null) values.add(node.paddingLeft);
    if (node.paddingRight != null) values.add(node.paddingRight);
    if (node.paddingTop != null) values.add(node.paddingTop);
    if (node.paddingBottom != null) values.add(node.paddingBottom);
    if (node.itemSpacing != null) values.add(node.itemSpacing);
    if (node.counterAxisSpacing != null) values.add(node.counterAxisSpacing);
  });

  return Array.from(values).filter((v) => v > 0).sort((a, b) => a - b).slice(0, 15);
}

/** Extract component names and types. */
function extractComponents(data) {
  const components = [];
  const seen = new Set();
  const doc = data.document || data;

  walkNodes(doc, (node) => {
    if (
      (node.type === 'COMPONENT' || node.type === 'COMPONENT_SET') &&
      node.name && !node.name.startsWith('_') && !seen.has(node.name)
    ) {
      seen.add(node.name);
      components.push({
        name: node.name,
        type: node.type === 'COMPONENT_SET' ? 'variant_set' : 'component',
      });
    }
  });

  return components.slice(0, 30);
}

/** Extract page and frame layout structure. */
function extractLayouts(data) {
  const layouts = [];
  const doc = data.document || data;

  for (const page of (doc.children || [])) {
    if (page.type !== 'CANVAS') continue;
    const frames = [];
    for (const frame of (page.children || []).slice(0, 10)) {
      if (frame.type === 'FRAME' || frame.type === 'SECTION') {
        frames.push({
          name: frame.name,
          width: frame.absoluteBoundingBox?.width || null,
          height: frame.absoluteBoundingBox?.height || null,
          layoutMode: frame.layoutMode || null,
        });
      }
    }
    if (frames.length) layouts.push({ page: page.name, frames });
  }

  return layouts.slice(0, 10);
}
