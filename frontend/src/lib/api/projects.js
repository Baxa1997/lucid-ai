// ─────────────────────────────────────────────────────────
//  Lucid AI — project data hooks for the workspace page.
//  Plain fetch + useState (matches the rest of the codebase, which
//  doesn't pull in SWR or React Query).
//
//  Each hook returns { data, error, status, refetch } where:
//    • status ∈ "idle" | "loading" | "success" | "error"
//    • error  is one of: "404" | "403" | "5xx" | "network" | null
//
//  Hooks DO NOT cache across mounts — each component instance kicks off
//  its own fetch. Good enough for the workspace page (mounted per route
//  visit). When this gets expensive we'll layer SWR on top.
// ─────────────────────────────────────────────────────────

import { useEffect, useState, useCallback, useRef } from "react";

// Map HTTP status to the small set of error codes the UI cares about.
function classifyError(status) {
  if (status === 404) return "404";
  if (status === 403) return "403";
  if (status >= 500) return "5xx";
  if (status >= 400) return "client";
  return "unknown";
}

function useFetchJson(buildUrl) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [status, setStatus] = useState("idle");
  // Latest-only: ignore responses from stale fetches when buildUrl changes.
  const reqIdRef = useRef(0);

  const run = useCallback(async () => {
    const url = buildUrl();
    if (!url) {
      setStatus("idle");
      return;
    }
    const myReqId = ++reqIdRef.current;
    setStatus("loading");
    setError(null);
    try {
      const res = await fetch(url);
      if (myReqId !== reqIdRef.current) return;  // stale
      if (!res.ok) {
        setError(classifyError(res.status));
        setStatus("error");
        return;
      }
      const json = await res.json();
      if (myReqId !== reqIdRef.current) return;  // stale
      setData(json);
      setStatus("success");
    } catch (err) {
      if (myReqId !== reqIdRef.current) return;
      console.error("Fetch failed:", err);
      setError("network");
      setStatus("error");
    }
  }, [buildUrl]);

  useEffect(() => { run(); }, [run]);

  return { data, error, status, refetch: run };
}

// ── Hooks ─────────────────────────────────────────────────────────────

export function useProject(projectId) {
  const buildUrl = useCallback(
    () => (projectId ? `/api/projects/${encodeURIComponent(projectId)}` : null),
    [projectId],
  );
  return useFetchJson(buildUrl);
}

export function useProjectMessages(projectId, { limit = 50, offset = 0 } = {}) {
  const buildUrl = useCallback(
    () => projectId
      ? `/api/projects/${encodeURIComponent(projectId)}/messages?limit=${limit}&offset=${offset}`
      : null,
    [projectId, limit, offset],
  );
  return useFetchJson(buildUrl);
}

export function useProjectFiles(projectId) {
  const buildUrl = useCallback(
    () => (projectId ? `/api/projects/${encodeURIComponent(projectId)}/files` : null),
    [projectId],
  );
  return useFetchJson(buildUrl);
}
