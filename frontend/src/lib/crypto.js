// ─────────────────────────────────────────────────────────
//  u-code — Encryption Utils
//  AES-256-CBC with per-message IV
// ─────────────────────────────────────────────────────────

import crypto from 'crypto';

const ALGORITHM = 'aes-256-cbc';

// Helper: Ensure the secret is exactly 32 bytes (256 bits)
function getSecretKey() {
  const secret = process.env.ENCRYPTION_KEY;
  if (!secret) {
    throw new Error('Missing ENCRYPTION_KEY environment variable');
  }
  // Hash the secret to ensure it's always 32 bytes
  return crypto.createHash('sha256').update(String(secret)).digest();
}

/**
 * Encrypt a plaintext string.
 * Returns { encrypted: hex, iv: hex }
 *
 * @param {string} text
 * @returns {{ encrypted: string, iv: string }}
 */
export function encrypt(text) {
  const iv = crypto.randomBytes(16);
  const key = getSecretKey();
  const cipher = crypto.createCipheriv(ALGORITHM, key, iv);

  let encrypted = cipher.update(text, 'utf8', 'hex');
  encrypted += cipher.final('hex');

  return {
    encrypted,
    iv: iv.toString('hex'),
  };
}

/**
 * Decrypt a hex string using the stored IV.
 * Returns the original plaintext.
 *
 * @param {string} encryptedHex
 * @param {string} ivHex
 * @returns {string}
 */
export function decrypt(encryptedHex, ivHex) {
  const iv = Buffer.from(ivHex, 'hex');
  const key = getSecretKey();
  const decipher = crypto.createDecipheriv(ALGORITHM, key, iv);

  let decrypted = decipher.update(encryptedHex, 'hex', 'utf8');
  decrypted += decipher.final('utf8');

  return decrypted;
}
