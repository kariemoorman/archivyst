"""
Tests for code file parsing and conversion.

Tests the Python ast parser, raw fallback, markdown assembly,
and code manifest/scan operations.
"""

import textwrap
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from archivyst.core.code_converter import compute_source_hash, convert_code_to_markdown
from archivyst.core.code_parser import (
    EXTENSION_MAP,
    HAS_TREE_SITTER,
    get_all_code_extensions,
    parse_code_file,
    parse_python,
    parse_raw,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def py_simple(tmp_path: Path) -> Path:
    """A simple Python file with a function."""
    f = tmp_path / "simple.py"
    f.write_text(textwrap.dedent('''\
        """Module docstring."""

        import os
        from pathlib import Path

        def hello(name: str) -> str:
            """Say hello."""
            return f"Hello {name}"
        '''))
    return f


@pytest.fixture
def py_class(tmp_path: Path) -> Path:
    """A Python file with a class containing methods."""
    f = tmp_path / "greeter.py"
    f.write_text(textwrap.dedent('''\
        class Greeter:
            """A greeter class."""

            def __init__(self, prefix: str = "Hi"):
                """Initialize with prefix."""
                self.prefix = prefix

            def greet(self, name: str) -> str:
                """Greet someone."""
                return f"{self.prefix} {name}"

            async def async_greet(self, name: str) -> str:
                return f"async {self.prefix} {name}"
        '''))
    return f


@pytest.fixture
def py_visibility(tmp_path: Path) -> Path:
    """A Python file with various visibility levels."""
    f = tmp_path / "visibility.py"
    f.write_text(textwrap.dedent("""\
        def public_func():
            pass

        def _internal_func():
            pass

        def __private_func():
            pass

        class _InternalClass:
            pass
        """))
    return f


@pytest.fixture
def py_empty(tmp_path: Path) -> Path:
    """An empty Python file."""
    f = tmp_path / "empty.py"
    f.write_text("")
    return f


@pytest.fixture
def py_syntax_error(tmp_path: Path) -> Path:
    """A Python file with a syntax error."""
    f = tmp_path / "broken.py"
    f.write_text("def broken(:\n    pass\n")
    return f


@pytest.fixture
def c_file(tmp_path: Path) -> Path:
    """A simple C file."""
    f = tmp_path / "utils.c"
    f.write_text(textwrap.dedent("""\
        #include <stdio.h>
        #include <stdlib.h>

        struct Point {
            int x;
            int y;
        };

        int add(int a, int b) {
            return a + b;
        }
        """))
    return f


@pytest.fixture
def sql_file(tmp_path: Path) -> Path:
    """A simple SQL file."""
    f = tmp_path / "billing.sql"
    f.write_text(textwrap.dedent("""\
        CREATE OR REPLACE PROCEDURE calculate_invoice(
            p_customer_id NUMBER,
            p_period DATE
        ) AS
        BEGIN
            NULL;
        END;
        /

        CREATE OR REPLACE FUNCTION get_balance(
            p_customer_id NUMBER
        ) RETURN NUMBER AS
        BEGIN
            RETURN 0;
        END;
        /
        """))
    return f


# ---------------------------------------------------------------------------
# Extension map tests
# ---------------------------------------------------------------------------


def test_extension_map_has_python():
    assert EXTENSION_MAP["py"] == "python"


def test_extension_map_has_c():
    assert EXTENSION_MAP["c"] == "c"
    assert EXTENSION_MAP["h"] == "c"


def test_extension_map_has_sql():
    assert EXTENSION_MAP["sql"] == "sql"


def test_get_all_code_extensions():
    exts = get_all_code_extensions()
    assert "py" in exts
    assert "c" in exts
    assert "java" in exts
    assert "sql" in exts
    assert "md" not in exts
    assert "csv" not in exts


# ---------------------------------------------------------------------------
# Python ast parser tests
# ---------------------------------------------------------------------------


def test_parse_python_simple_function(py_simple: Path):
    result = parse_python(py_simple)

    assert result.parse_method == "ast"
    assert result.language == "python"
    assert result.file_docstring == "Module docstring."

    # Imports
    assert "os" in result.imports
    assert "pathlib.Path" in result.imports

    # Symbols
    assert len(result.symbols) == 1
    func = result.symbols[0]
    assert func.kind == "function"
    assert func.name == "hello"
    assert "name: str" in func.signature
    assert "-> str" in func.signature
    assert func.docstring == "Say hello."
    assert func.visibility == "public"


def test_parse_python_class_with_methods(py_class: Path):
    result = parse_python(py_class)

    assert len(result.symbols) == 1
    cls = result.symbols[0]
    assert cls.kind == "class"
    assert cls.name == "Greeter"
    assert cls.docstring == "A greeter class."

    # Methods
    assert len(cls.children) == 3
    init_method = cls.children[0]
    assert init_method.kind == "method"
    assert init_method.name == "__init__"
    assert "prefix: str" in init_method.signature

    greet_method = cls.children[1]
    assert greet_method.name == "greet"
    assert greet_method.docstring == "Greet someone."

    async_method = cls.children[2]
    assert async_method.name == "async_greet"
    assert "async" in async_method.signature


def test_parse_python_visibility(py_visibility: Path):
    result = parse_python(py_visibility)

    symbols_by_name = {s.name: s for s in result.symbols}
    assert symbols_by_name["public_func"].visibility == "public"
    assert symbols_by_name["_internal_func"].visibility == "internal"
    assert symbols_by_name["__private_func"].visibility == "private"
    assert symbols_by_name["_InternalClass"].visibility == "internal"


def test_parse_python_empty_file(py_empty: Path):
    result = parse_python(py_empty)
    assert result.parse_method == "ast"
    assert result.symbols == []
    assert result.imports == []
    assert result.file_docstring is None


def test_parse_python_syntax_error(py_syntax_error: Path):
    result = parse_python(py_syntax_error)
    # Should fall back to raw
    assert result.parse_method == "raw"
    assert result.symbols == []


def test_parse_python_module_docstring(py_simple: Path):
    result = parse_python(py_simple)
    assert result.file_docstring == "Module docstring."


# ---------------------------------------------------------------------------
# Raw fallback tests
# ---------------------------------------------------------------------------


def test_parse_raw(tmp_path: Path):
    f = tmp_path / "test.zig"
    f.write_text("pub fn main() !void {}")
    result = parse_raw(f, "zig")

    assert result.parse_method == "raw"
    assert result.language == "zig"
    assert result.symbols == []
    assert result.imports == []


# ---------------------------------------------------------------------------
# parse_code_file routing tests
# ---------------------------------------------------------------------------


def test_parse_code_file_routes_python(py_simple: Path):
    result = parse_code_file(py_simple)
    assert result.parse_method == "ast"
    assert result.language == "python"


def test_parse_code_file_unknown_extension(tmp_path: Path):
    f = tmp_path / "test.xyz"
    f.write_text("unknown format")
    result = parse_code_file(f)
    assert result.parse_method == "raw"


def test_parse_code_file_size_guard(tmp_path: Path):
    f = tmp_path / "big.py"
    f.write_text("x = 1\n" * 100_000)  # ~600KB
    result = parse_code_file(f, max_file_size=1000)
    assert result.parse_method == "raw"


def test_parse_code_file_c_routes_correctly(c_file: Path):
    result = parse_code_file(c_file)
    if HAS_TREE_SITTER:
        assert result.parse_method == "tree-sitter"
    else:
        assert result.parse_method == "raw"


# ---------------------------------------------------------------------------
# Tree-sitter tests (skip if not installed)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not HAS_TREE_SITTER, reason="tree-sitter not installed")
def test_parse_tree_sitter_c(c_file: Path):
    result = parse_code_file(c_file)
    assert result.parse_method == "tree-sitter"
    assert result.language == "c"

    # Should find struct and function
    kinds = {s.kind for s in result.symbols}
    assert "function" in kinds or "struct" in kinds


@pytest.mark.skipif(not HAS_TREE_SITTER, reason="tree-sitter not installed")
def test_parse_tree_sitter_sql(sql_file: Path):
    result = parse_code_file(sql_file)
    assert result.parse_method == "tree-sitter"
    assert result.language == "sql"


# ---------------------------------------------------------------------------
# Markdown converter tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_convert_code_without_llm(py_class: Path):
    parsed = parse_python(py_class)
    md = await convert_code_to_markdown(parsed, llm_client=None)

    # Should have class heading
    assert "## Class: `Greeter`" in md
    # Should use docstring as fallback description
    assert "A greeter class." in md
    # Should have method headings
    assert "### `__init__" in md
    assert "### `greet" in md


@pytest.mark.asyncio
async def test_convert_code_with_llm_mock(py_simple: Path):
    parsed = parse_python(py_simple)

    mock_llm = AsyncMock()
    mock_llm.complete = AsyncMock(return_value="A greeting function that takes a name.")

    md = await convert_code_to_markdown(parsed, llm_client=mock_llm)

    # LLM description should appear
    assert "A greeting function" in md
    # LLM should have been called
    assert mock_llm.complete.called


@pytest.mark.asyncio
async def test_convert_code_raw_fallback(tmp_path: Path):
    f = tmp_path / "test.zig"
    f.write_text("pub fn main() !void {}")

    parsed = parse_raw(f, "zig")
    md = await convert_code_to_markdown(parsed, llm_client=None)

    # Should produce a code fence
    assert "```zig" in md
    assert "pub fn main()" in md


@pytest.mark.asyncio
async def test_convert_code_imports_section(py_simple: Path):
    parsed = parse_python(py_simple)
    md = await convert_code_to_markdown(parsed, llm_client=None)

    assert "## Imports" in md
    assert "os" in md
    assert "pathlib.Path" in md


@pytest.mark.asyncio
async def test_convert_code_file_docstring_as_summary(py_simple: Path):
    parsed = parse_python(py_simple)
    md = await convert_code_to_markdown(parsed, llm_client=None)

    # Module docstring should be used as file summary
    assert "Module docstring." in md


# ---------------------------------------------------------------------------
# Hash computation tests
# ---------------------------------------------------------------------------


def test_compute_source_hash(py_simple: Path):
    h = compute_source_hash(py_simple)
    assert len(h) == 64  # Full SHA-256 hex digest
    # Same file should produce same hash
    assert compute_source_hash(py_simple) == h


def test_compute_source_hash_different_files(py_simple: Path, py_class: Path):
    h1 = compute_source_hash(py_simple)
    h2 = compute_source_hash(py_class)
    assert h1 != h2


def test_compute_source_hash_nonexistent(tmp_path: Path):
    h = compute_source_hash(tmp_path / "nonexistent.py")
    assert h == ""


# ---------------------------------------------------------------------------
# Scan manifest tests
# ---------------------------------------------------------------------------


def test_load_empty_manifest(tmp_path: Path):
    from archivyst.cli.scan_cmd import load_code_manifest

    manifest = load_code_manifest(tmp_path)
    assert manifest == {}


def test_save_and_load_manifest(tmp_path: Path):
    from archivyst.cli.scan_cmd import load_code_manifest, save_code_manifest

    data = {
        "src/parser.py": {
            "generated_md": "src/parser.py.md",
            "source_hash": "abc123",
            "model": "test-model",
        }
    }
    save_code_manifest(tmp_path, data)
    loaded = load_code_manifest(tmp_path)
    assert loaded == data


def test_scan_detects_new_files(tmp_path: Path):
    from archivyst.cli.scan_cmd import scan_vault_for_code

    # Create vault with a code file
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "parser.py").write_text("def hello(): pass")

    pageindex = vault / ".pageindex"
    pageindex.mkdir()

    result = scan_vault_for_code(vault, pageindex, "test-model")
    assert len(result.new) == 1
    assert result.new[0].name == "parser.py"
    assert result.has_changes


def test_scan_detects_unchanged_files(tmp_path: Path):
    from archivyst.cli.scan_cmd import (
        save_code_manifest,
        scan_vault_for_code,
    )

    vault = tmp_path / "vault"
    vault.mkdir()
    code_file = vault / "parser.py"
    code_file.write_text("def hello(): pass")

    pageindex = vault / ".pageindex"
    pageindex.mkdir()

    # Create manifest with matching entry
    h = compute_source_hash(code_file)
    save_code_manifest(
        pageindex,
        {
            "parser.py": {
                "source_hash": h,
                "source_mtime": code_file.stat().st_mtime,
                "model": "test-model",
            }
        },
    )

    result = scan_vault_for_code(vault, pageindex, "test-model")
    assert len(result.unchanged) == 1
    assert len(result.new) == 0
    assert not result.has_changes


def test_scan_detects_deleted_files(tmp_path: Path):
    from archivyst.cli.scan_cmd import save_code_manifest, scan_vault_for_code

    vault = tmp_path / "vault"
    vault.mkdir()

    pageindex = vault / ".pageindex"
    pageindex.mkdir()

    # Manifest has an entry, but file doesn't exist
    save_code_manifest(
        pageindex,
        {
            "old.py": {
                "source_hash": "abc123",
                "source_mtime": 0,
                "model": "test-model",
            }
        },
    )

    result = scan_vault_for_code(vault, pageindex, "test-model")
    assert len(result.deleted) == 1
    assert result.deleted[0] == "old.py"
    assert result.has_changes


def test_scan_detects_model_change(tmp_path: Path):
    from archivyst.cli.scan_cmd import save_code_manifest, scan_vault_for_code

    vault = tmp_path / "vault"
    vault.mkdir()
    code_file = vault / "parser.py"
    code_file.write_text("def hello(): pass")

    pageindex = vault / ".pageindex"
    pageindex.mkdir()

    h = compute_source_hash(code_file)
    save_code_manifest(
        pageindex,
        {
            "parser.py": {
                "source_hash": h,
                "source_mtime": code_file.stat().st_mtime,
                "model": "old-model",
            }
        },
    )

    result = scan_vault_for_code(vault, pageindex, "new-model")
    assert result.model_changed
    assert len(result.unchanged) == 1  # File itself unchanged


def test_scan_skips_hidden_dirs(tmp_path: Path):
    from archivyst.cli.scan_cmd import scan_vault_for_code

    vault = tmp_path / "vault"
    vault.mkdir()
    hidden = vault / ".obsidian"
    hidden.mkdir()
    (hidden / "config.py").write_text("# obsidian config")
    (vault / "real.py").write_text("def real(): pass")

    pageindex = vault / ".pageindex"
    pageindex.mkdir()

    result = scan_vault_for_code(vault, pageindex, "test-model")
    assert len(result.new) == 1
    assert result.new[0].name == "real.py"


def test_scan_skips_large_files(tmp_path: Path):
    from archivyst.cli.scan_cmd import scan_vault_for_code

    vault = tmp_path / "vault"
    vault.mkdir()
    big = vault / "big.py"
    big.write_text("x = 1\n" * 50_000)  # ~300KB

    pageindex = vault / ".pageindex"
    pageindex.mkdir()

    result = scan_vault_for_code(vault, pageindex, "test-model", max_file_size=1000)
    assert len(result.new) == 0  # Skipped due to size
