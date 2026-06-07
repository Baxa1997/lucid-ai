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

const PROJECT_TYPES = [
  ["admin dashboard", /\badmin\s+dashboard\b/i],
  ["landing page", /\blanding\s+page\b/i],
  ["booking platform", /\bbooking\s+(?:app|site|platform|system)\b/i],
  ["online store", /\b(?:online\s+store|e-?commerce)\b/i],
  ["web app", /\bweb\s+app(?:lication)?\b/i],
  ["website", /\b(?:website|web\s*site)\b/i],
  ["portfolio", /\bportfolio\b/i],
  ["dashboard", /\bdashboard\b/i],
  ["marketplace", /\bmarketplace\b/i],
  ["directory", /\bdirectory\b/i],
  ["portal", /\bportal\b/i],
  ["blog", /\bblog\b/i],
  ["CRM", /\bcrm\b/i],
  ["app", /\bapp(?:lication)?\b/i],
];

const DOMAIN_STOPWORDS = new Set([
  "add", "and", "app", "application", "beautiful", "better", "build", "change",
  "clean", "cool", "create", "dark", "design", "for", "full", "good", "great",
  "landing", "light", "make", "modern", "mode", "my", "need", "new", "nice",
  "nicer", "page", "please", "professional", "project", "responsive", "simple",
  "site", "something", "the", "theme", "want", "web", "website", "with",
]);

const DOMAIN_CUES = new Set([
  "academy", "agency", "bakery", "barber", "booking", "center", "clinic",
  "coach", "coffee", "company", "course", "dentist", "dental", "designer",
  "developer", "education", "eduction", "event", "finance", "fitness", "hotel",
  "inventory", "law", "logistics", "market", "nonprofit", "photographer",
  "portfolio", "restaurant", "sales", "school", "shop", "startup", "studio",
  "store", "todo", "travel", "university",
]);

const COMMON_GIBBERISH = new Set([
  "asdf", "asdfasdf", "dasdas", "dfgdfg", "hhhh", "qwerty", "qwrt",
]);

function userTurns(prompt, history = []) {
  return [
    ...history
      .filter((message) => message?.role === "user" && message?.content)
      .map((message) => String(message.content).trim()),
    String(prompt || "").trim(),
  ].filter(Boolean);
}

function findProjectType(text) {
  let earliest = null;
  for (const [name, pattern] of PROJECT_TYPES) {
    const match = text.match(pattern);
    if (match && (!earliest || match.index < earliest.match.index)) {
      earliest = { name, match };
    }
  }
  return earliest;
}

function cleanDomain(raw) {
  return String(raw || "")
    .toLowerCase()
    .replace(/\b(?:admin\s+dashboard|landing\s+page|booking\s+(?:app|site|platform|system)|online\s+store|e-?commerce|web\s+app(?:lication)?|web\s*site)\b/g, " ")
    .replace(/\b(?:dashboard|marketplace|directory|portfolio|portal|website|site|blog|crm|app(?:lication)?)\b/g, " ")
    .replace(/[^a-z0-9\s-]/g, " ")
    .split(/\s+/)
    .filter((token) => {
      if (!token || DOMAIN_STOPWORDS.has(token) || COMMON_GIBBERISH.has(token)) return false;
      const letters = token.match(/[a-z]/g) || [];
      if (letters.length < 3) return false;
      const vowels = letters.filter((letter) => VOWELS.includes(letter)).length;
      return vowels / letters.length >= 0.15;
    })
    .join(" ")
    .trim();
}

function hasDomainCue(domain) {
  return domain.split(/\s+/).some((token) => DOMAIN_CUES.has(token));
}

function sentenceFor(type, domain) {
  const article = /^[aeiou]/i.test(type) ? "An" : "A";
  return `${article} ${type} for ${domain}.`;
}

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

// Conservative outage fallback for the new-project intent gate. It only
// approves prompts where a project type and a meaningful subject can both be
// extracted without semantic guessing. Gemini remains responsible for vague
// or unusual descriptions.
export function inferExplicitProjectIntent(prompt, history = []) {
  const turns = userTurns(prompt, history);
  const combined = turns.join(" ").replace(/\s+/g, " ").trim();
  const projectType = findProjectType(combined);
  if (!projectType) return null;

  // The strongest deterministic signal is an explicit "for/about" subject.
  const explicitSubject = combined.match(/\b(?:for|about)\s+(?:my\s+|an?\s+|the\s+)?(.+)$/i);
  let domain = cleanDomain(explicitSubject?.[1]);

  // Also support natural type-last prompts such as "coffee shop landing page".
  if (!domain && projectType.match.index > 0) {
    domain = cleanDomain(combined.slice(0, projectType.match.index));
  }

  // Multi-turn intake: "landing page" followed by "education center".
  if (!domain) {
    const domainTurn = turns.find((turn) => !findProjectType(turn) && hasDomainCue(cleanDomain(turn)));
    domain = cleanDomain(domainTurn);
  }

  // Permit recognizable functional subjects such as "todo app", but avoid
  // treating styling-only text like "app with dark mode" as a domain.
  if (!domain) {
    const cleanedCombined = cleanDomain(combined);
    if (hasDomainCue(cleanedCombined)) domain = cleanedCombined;
  }

  if (!domain) return null;
  return {
    isProject: true,
    summary: sentenceFor(projectType.name, domain),
  };
}
