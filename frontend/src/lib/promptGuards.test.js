import { describe, expect, it } from "vitest";
import { inferExplicitProjectIntent, validatePromptShape } from "./promptGuards";

describe("validatePromptShape", () => {
  it("rejects repeated random text", () => {
    expect(validatePromptShape("hhhh").ok).toBe(false);
    expect(validatePromptShape("asdfasdfasdf").ok).toBe(false);
  });

  it("accepts a complete project prompt", () => {
    expect(validatePromptShape("landing page for eduction center").ok).toBe(true);
  });
});

describe("inferExplicitProjectIntent", () => {
  it("accepts an explicit type and subject", () => {
    expect(inferExplicitProjectIntent("landing page for eduction center")).toEqual({
      isProject: true,
      summary: "A landing page for eduction center.",
    });
  });

  it("preserves a subject that also contains a project-type word", () => {
    expect(inferExplicitProjectIntent("landing page for coffee shop")?.summary).toBe(
      "A landing page for coffee shop.",
    );
    expect(inferExplicitProjectIntent("website for a coffee shop")?.summary).toBe(
      "A website for coffee shop.",
    );
  });

  it("ignores prior gibberish when the latest prompt is complete", () => {
    const result = inferExplicitProjectIntent("landing page for education center", [
      { role: "user", content: "hhhh" },
      { role: "assistant", content: "What would you like to build?" },
    ]);
    expect(result?.isProject).toBe(true);
    expect(result?.summary).toBe("A landing page for education center.");
  });

  it("combines a type and subject supplied across turns", () => {
    const result = inferExplicitProjectIntent("education center", [
      { role: "user", content: "landing page" },
    ]);
    expect(result).toEqual({
      isProject: true,
      summary: "A landing page for education center.",
    });
  });

  it.each(["hhhh", "landing page", "education center"])(
    "rejects incomplete or random input: %s",
    (prompt) => {
      expect(inferExplicitProjectIntent(prompt)).toBeNull();
    },
  );

  it("does not treat gibberish as a multi-turn subject", () => {
    expect(inferExplicitProjectIntent("asdf", [
      { role: "user", content: "landing page" },
    ])).toBeNull();
  });
});
