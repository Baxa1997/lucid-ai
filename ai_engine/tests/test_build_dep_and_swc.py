"""Two production fixes that came out of the home-renting preview saga
(2026-06-13):

1. BuildValidator auto-adds a bare package that's imported but absent from
   package.json (e.g. `import dayjs`) instead of burning a code-fix attempt.
2. The node_modules cache integrity check detects a *truncated* native binary
   (a 15 MB @next/swc where the real one is ~135 MB — a large write truncating
   over the macOS FUSE bind mount) WITHOUT executing it (executing a truncated
   .node SIGBUSes the process).
"""
import struct

from app.services.build_validator import BuildValidator
from app.services.pipeline.orchestrator import (
    _node_binary_truncated,
    _first_truncated_native,
)


def _validator():
    return BuildValidator(api_key="", classification={}, websocket=None, max_retries=0)


class TestMissingBareModules:
    def test_detects_undeclared_dayjs(self):
        errors = "Module not found: Can't resolve 'dayjs'\n./src/components/HeroSearchSection.jsx"
        pkg = {"dependencies": {"next": "^15", "react": "^19"}}
        assert _validator()._missing_bare_modules(errors, pkg) == ["dayjs"]

    def test_ignores_relative_and_alias_and_builtins(self):
        errors = (
            "Can't resolve './Foo'\n"
            "Can't resolve '../bar/Baz'\n"
            "Can't resolve '@/components/Nav'\n"
            "Cannot find module 'fs'\n"
            "Cannot find module 'node:path'\n"
        )
        assert _validator()._missing_bare_modules(errors, {}) == []

    def test_skips_already_declared_and_framework(self):
        errors = "Can't resolve 'next'\nCan't resolve 'lodash'"
        pkg = {"dependencies": {"lodash": "^4"}}
        # next is framework (install failure, not a missing dep); lodash already declared
        assert _validator()._missing_bare_modules(errors, pkg) == []

    def test_collapses_deep_and_scoped_imports(self):
        errors = (
            "Can't resolve 'dayjs/plugin/utc'\n"
            "Can't resolve '@tanstack/react-query/devtools'\n"
        )
        got = _validator()._missing_bare_modules(errors, {})
        assert got == ["dayjs", "@tanstack/react-query"]


def _elf64_header(*, e_phoff=0, e_phnum=0, e_phentsize=56,
                  e_shoff=0, e_shnum=0, e_shentsize=64) -> bytes:
    h = bytearray(64)
    h[0:4] = b"\x7fELF"
    h[4] = 2  # ELFCLASS64
    h[5] = 1  # little-endian
    struct.pack_into("<Q", h, 0x20, e_phoff)
    struct.pack_into("<Q", h, 0x28, e_shoff)
    struct.pack_into("<H", h, 0x36, e_phentsize)
    struct.pack_into("<H", h, 0x38, e_phnum)
    struct.pack_into("<H", h, 0x3A, e_shentsize)
    struct.pack_into("<H", h, 0x3C, e_shnum)
    return bytes(h)


class TestNativeBinaryTruncation:
    def test_truncated_when_section_table_past_eof(self, tmp_path):
        # Header claims a section table at offset 1 MB but the file is 64 bytes.
        f = tmp_path / "next-swc.node"
        f.write_bytes(_elf64_header(e_shoff=1_000_000, e_shnum=1, e_shentsize=64))
        assert _node_binary_truncated(str(f)) is True

    def test_truncated_when_segment_extends_past_eof(self, tmp_path):
        # One PT_LOAD program header whose p_offset+p_filesz exceeds the file.
        hdr = _elf64_header(e_phoff=64, e_phnum=1, e_phentsize=56)
        ph = bytearray(56)
        struct.pack_into("<Q", ph, 0x08, 0)          # p_offset
        struct.pack_into("<Q", ph, 0x20, 50_000_000)  # p_filesz (claims 50 MB)
        f = tmp_path / "big.node"
        f.write_bytes(hdr + bytes(ph))  # only ~120 bytes on disk
        assert _node_binary_truncated(str(f)) is True

    def test_intact_when_file_covers_headers(self, tmp_path):
        hdr = _elf64_header(e_shoff=64, e_shnum=1, e_shentsize=64)  # needs 128 bytes
        f = tmp_path / "ok.node"
        f.write_bytes(hdr + b"\x00" * 200)  # 264 bytes ≥ 128
        assert _node_binary_truncated(str(f)) is False

    def test_non_elf_is_not_flagged(self, tmp_path):
        f = tmp_path / "weird.node"
        f.write_bytes(b"not an elf file at all" * 10)
        assert _node_binary_truncated(str(f)) is False

    def test_first_truncated_native_walks_tree(self, tmp_path):
        good = tmp_path / "a"
        good.mkdir()
        (good / "ok.node").write_bytes(_elf64_header() + b"\x00" * 100)
        bad = tmp_path / "b" / "@next" / "swc"
        bad.mkdir(parents=True)
        (bad / "next-swc.node").write_bytes(
            _elf64_header(e_shoff=999_999, e_shnum=1, e_shentsize=64)
        )
        found = _first_truncated_native(str(tmp_path))
        assert found is not None and found.endswith("next-swc.node")

    def test_first_truncated_native_clean_tree_returns_none(self, tmp_path):
        (tmp_path / "ok.node").write_bytes(_elf64_header() + b"\x00" * 100)
        assert _first_truncated_native(str(tmp_path)) is None
