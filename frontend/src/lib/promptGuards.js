// ─────────────────────────────────────────────────────────
//  Cheap regex pre-filter for the dashboard composer.
//
//  Mirrors the structural checks in ai_engine's step1_validate.py
//  (length, repeat patterns, vowel ratio, single-token). Runs BEFORE
//  the /api/intent-check Gemini call so obvious gibberish bails out
//  in <1ms — saves the Vertex round trip + token cost on the common
//  "dasdasdas" case.
//
//  Returns:
//    { ok: true }                  — looks plausible; defer to Gemini
//    { ok: false, message }        — gibberish; show inline notice
//
//  The reject `message` matches the unified clarify text used by
//  /api/intent-check fail-closed and useAgentSession.js legacy rescue
//  so the user sees identical copy regardless of which layer caught
//  the prompt.
// ─────────────────────────────────────────────────────────

const REPEAT_RE = /^(.{1,4})\1{2,}$/;
const VOWELS = "aeiou";

const UNIFIED_REPLY =
  "I couldn't quite read that. Could you describe what you'd like to build? " +
  'For example: "a landing page for my coffee shop" or "a portfolio site for a freelance designer".';

export function validatePromptShape(raw) {
  const text = (raw || "").trim().toLowerCase();

  // Too short to carry intent — single word like "hi" or "app".
  if (text.length < 8) {
    return { ok: false, message: UNIFIED_REPLY };
  }

  // Repeated cluster like "dasdasdas" / "asdfasdf".
  const compact = text.replace(/\s+/g, "");
  if (REPEAT_RE.test(compact)) {
    return { ok: false, message: UNIFIED_REPLY };
  }

  // Almost-no-vowels consonant mash like "qwrtqwrt" / "dfgdfg".
  const letters = text.match(/[a-z]/g) || [];
  if (letters.length > 0) {
    const vowelCount = letters.filter((c) => VOWELS.includes(c)).length;
    if (vowelCount / letters.length < 0.15) {
      return { ok: false, message: UNIFIED_REPLY };
    }
  }

  // Need at least 2 distinct ≥3-char tokens. Real prompts like
  // "coffee shop" / "todo app" pass; lone "website" or "app" don't.
  const tokens = new Set(text.match(/[a-z]{3,}/g) || []);
  if (tokens.size < 2) {
    return { ok: false, message: UNIFIED_REPLY };
  }

  return { ok: true };
}
