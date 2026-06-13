"""A local build-worker crash (SIGBUS / OOM) is an INFRA failure of the build
sandbox, not a code error. It must not demote a valid project to a draft branch
+ a broken Vercel deploy (Pitch & Pavilion / home-renting landing 2026-06-13:
`next build worker exited with code: null and signal: SIGBUS` → staging-only →
empty main → Vercel 400).
"""
import asyncio
import subprocess

from app.services.build_validator import BuildValidator


class _FakeProc:
    def __init__(self, returncode, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _validator():
    return BuildValidator(api_key="", classification={}, websocket=None, max_retries=0)


class TestRunBuildClassification:
    def test_sigbus_worker_crash_is_infra(self, monkeypatch, tmp_path):
        proc = _FakeProc(
            1,
            stdout="Creating an optimized production build ...",
            stderr="Next.js build worker exited with code: null and signal: SIGBUS",
        )
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: proc)
        res = asyncio.run(_validator().run_build(str(tmp_path), ["next", "build"], {}))
        assert res["success"] is False
        assert res.get("infra_crash") is True
        assert res.get("error_count") == 0

    def test_oom_is_infra(self, monkeypatch, tmp_path):
        proc = _FakeProc(1, stderr="FATAL ERROR: JavaScript heap out of memory")
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: proc)
        res = asyncio.run(_validator().run_build(str(tmp_path), ["next", "build"], {}))
        assert res.get("infra_crash") is True

    def test_real_compile_error_is_not_infra(self, monkeypatch, tmp_path):
        proc = _FakeProc(
            1,
            stdout="Failed to compile.",
            stderr="Module not found: Can't resolve '@/components/Missing'",
        )
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: proc)
        res = asyncio.run(_validator().run_build(str(tmp_path), ["next", "build"], {}))
        assert res["success"] is False
        assert not res.get("infra_crash")

    def test_crash_with_real_error_present_is_not_infra(self, monkeypatch, tmp_path):
        # a SIGBUS line AND a real compile error → treat as a code error (safer)
        proc = _FakeProc(
            1,
            stderr="signal: SIGBUS\nModule not found: Can't resolve 'x'",
        )
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: proc)
        res = asyncio.run(_validator().run_build(str(tmp_path), ["next", "build"], {}))
        assert not res.get("infra_crash")

    def test_success_passes_through(self, monkeypatch, tmp_path):
        proc = _FakeProc(0, stdout="Compiled successfully")
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: proc)
        res = asyncio.run(_validator().run_build(str(tmp_path), ["next", "build"], {}))
        assert res["success"] is True


class TestGenerationBuildNonBlocking:
    def test_infra_crash_keeps_build_ok_true(self, monkeypatch):
        from app.services import generation_build as gb

        class _WS:
            pass
        ws = _WS()

        async def _fake_validate(self, workspace_path):
            return {"success": False, "infra_crash": True, "error_count": 0, "fixed_files": []}

        monkeypatch.setattr(
            "app.services.build_validator.BuildValidator.validate_and_fix", _fake_validate,
        )
        result = asyncio.run(gb.run_generation_build_check(
            pipeline="landing", workspace_path="/tmp/x",
            api_key="", classification={}, websocket=ws,
        ))
        # a real infra crash must NOT block publish — code is valid, deploy
        # build runs elsewhere
        assert result.get("build_ok") is True
        assert getattr(ws, "_build_ok") is True

    def test_real_failure_blocks_publish(self, monkeypatch):
        from app.services import generation_build as gb

        class _WS:
            pass
        ws = _WS()

        async def _fake_validate(self, workspace_path):
            return {"success": False, "error_count": 2, "fixed_files": []}

        monkeypatch.setattr(
            "app.services.build_validator.BuildValidator.validate_and_fix", _fake_validate,
        )
        asyncio.run(gb.run_generation_build_check(
            pipeline="landing", workspace_path="/tmp/x",
            api_key="", classification={}, websocket=ws,
        ))
        assert getattr(ws, "_build_ok") is False
