"""
Code file structure extraction.

Parses source code files into structural elements (classes, functions, imports)
for conversion to structured markdown notes. Uses Python's ast module for .py
files and tree-sitter for all other languages, with a raw code fence fallback.
"""

import ast
import contextlib
import logging
import textwrap
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# Tree-sitter availability (guarded import)
HAS_TREE_SITTER = False
try:
    import tree_sitter_language_pack  # noqa: F401

    HAS_TREE_SITTER = True
except ImportError:
    pass


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class CodeSymbol:
    """A structural element extracted from source code."""

    kind: str  # "class", "function", "method", "struct", "procedure", etc.
    name: str
    signature: str
    source_code: str
    line_start: int
    line_end: int
    docstring: str | None = None
    children: list["CodeSymbol"] = field(default_factory=list)
    visibility: str = "public"  # "public", "private", "internal"


@dataclass
class ParsedCodeFile:
    """Result of parsing a source code file."""

    filepath: Path
    language: str
    imports: list[str]
    symbols: list[CodeSymbol]
    parse_method: str  # "ast", "tree-sitter", "raw"
    file_docstring: str | None = None


# ---------------------------------------------------------------------------
# Extension → language mapping
# ---------------------------------------------------------------------------

EXTENSION_MAP: dict[str, str] = {
    # Systems
    "c": "c",
    "h": "c",
    "cpp": "cpp",
    "cc": "cpp",
    "cxx": "cpp",
    "hpp": "cpp",
    "hh": "cpp",
    "rs": "rust",
    "go": "go",
    "zig": "zig",
    # JVM
    "java": "java",
    "kt": "kotlin",
    "scala": "scala",
    "groovy": "groovy",
    # .NET
    "cs": "c_sharp",
    "fs": "fsharp",
    "vb": "vb",
    # Scripting
    "py": "python",
    "rb": "ruby",
    "pl": "perl",
    "lua": "lua",
    "r": "r",
    # Web
    "js": "javascript",
    "mjs": "javascript",
    "ts": "typescript",
    "tsx": "typescript",
    "html": "html",
    "css": "css",
    "scss": "scss",
    "php": "php",
    # Data / Query
    "sql": "sql",
    # Mainframe
    "cob": "cobol",
    "cbl": "cobol",
    "cpy": "cobol",
    "f90": "fortran",
    "f95": "fortran",
    "f03": "fortran",
    # Shell
    "sh": "bash",
    "bash": "bash",
    "zsh": "bash",
    "ps1": "powershell",
    # Functional
    "hs": "haskell",
    "ex": "elixir",
    "exs": "elixir",
    "erl": "erlang",
    "ml": "ocaml",
    "clj": "clojure",
    # Mobile
    "swift": "swift",
    "dart": "dart",
    "m": "objc",
    # Config / IaC
    "tf": "hcl",
    # Markup
    "xml": "xml",
    "tex": "latex",
}


# ---------------------------------------------------------------------------
# Tree-sitter node type mapping (per language)
# ---------------------------------------------------------------------------

TREE_SITTER_NODE_TYPES: dict[str, dict[str, list[str]]] = {
    "c": {
        "function": ["function_definition"],
        "struct": ["struct_specifier"],
        "import": ["preproc_include"],
        "enum": ["enum_specifier"],
    },
    "cpp": {
        "class": ["class_specifier"],
        "function": ["function_definition"],
        "struct": ["struct_specifier"],
        "import": ["preproc_include"],
        "enum": ["enum_specifier"],
    },
    "java": {
        "class": ["class_declaration"],
        "interface": ["interface_declaration"],
        "function": ["method_declaration", "constructor_declaration"],
        "import": ["import_declaration"],
        "enum": ["enum_declaration"],
    },
    "javascript": {
        "class": ["class_declaration"],
        "function": ["function_declaration", "method_definition"],
        "import": ["import_statement"],
    },
    "typescript": {
        "class": ["class_declaration"],
        "interface": ["interface_declaration"],
        "function": ["function_declaration", "method_definition"],
        "import": ["import_statement"],
        "enum": ["enum_declaration"],
    },
    "go": {
        "struct": ["type_declaration"],
        "interface": ["type_declaration"],
        "function": ["function_declaration", "method_declaration"],
        "import": ["import_declaration"],
    },
    "rust": {
        "struct": ["struct_item"],
        "trait": ["trait_item"],
        "function": ["function_item"],
        "import": ["use_declaration"],
        "enum": ["enum_item"],
        "impl": ["impl_item"],
    },
    "sql": {
        "procedure": [
            "create_procedure_statement",
            "create_or_replace_procedure",
        ],
        "function": [
            "create_function_statement",
            "create_or_replace_function",
        ],
        "trigger": ["create_trigger_statement"],
        "view": ["create_view_statement"],
        "package": ["create_package_statement"],
    },
    "c_sharp": {
        "class": ["class_declaration"],
        "interface": ["interface_declaration"],
        "struct": ["struct_declaration"],
        "function": ["method_declaration", "constructor_declaration"],
        "import": ["using_directive"],
        "enum": ["enum_declaration"],
    },
    "kotlin": {
        "class": ["class_declaration"],
        "function": ["function_declaration"],
        "import": ["import_header"],
    },
    "ruby": {
        "class": ["class"],
        "function": ["method"],
        "import": ["call"],  # require/include
    },
    "swift": {
        "class": ["class_declaration"],
        "struct": ["struct_declaration"],
        "function": ["function_declaration"],
        "import": ["import_declaration"],
        "enum": ["enum_declaration"],
        "protocol": ["protocol_declaration"],
    },
    "bash": {
        "function": ["function_definition"],
    },
    "php": {
        "class": ["class_declaration"],
        "interface": ["interface_declaration"],
        "function": ["function_definition", "method_declaration"],
        "import": ["namespace_use_declaration"],
    },
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_all_code_extensions() -> set[str]:
    """Return all file extensions recognized as code files."""
    return set(EXTENSION_MAP.keys())


def parse_code_file(source: Path, max_file_size: int = 512_000) -> ParsedCodeFile:
    """Parse a code file and extract its structure.

    Routes to the appropriate parser based on file extension:
    - .py → Python ast parser (stdlib)
    - Others → tree-sitter (if installed and language supported)
    - Fallback → raw (no structure extraction)

    Args:
        source: Path to the source code file.
        max_file_size: Maximum file size in bytes (default 500KB).

    Returns:
        ParsedCodeFile with extracted symbols and metadata.
    """
    ext = source.suffix.lower().lstrip(".")
    language = EXTENSION_MAP.get(ext)

    if language is None:
        logger.warning("[code_parser] Unknown extension: .%s", ext)
        return parse_raw(source, ext)

    # File size guard
    try:
        file_size = source.stat().st_size
    except OSError as e:
        logger.warning("[code_parser] Cannot stat %s: %s", source, e)
        return parse_raw(source, language)

    if file_size > max_file_size:
        logger.warning(
            "[code_parser] File too large (%d bytes > %d): %s",
            file_size,
            max_file_size,
            source,
        )
        return parse_raw(source, language)

    # Route to appropriate parser
    if language == "python":
        return parse_python(source)

    if HAS_TREE_SITTER and language in TREE_SITTER_NODE_TYPES:
        return parse_with_tree_sitter(source, language)

    return parse_raw(source, language)


# ---------------------------------------------------------------------------
# Python ast parser
# ---------------------------------------------------------------------------


def _python_visibility(name: str) -> str:
    """Determine visibility from Python naming conventions."""
    if name.startswith("__") and not name.endswith("__"):
        return "private"
    if name.startswith("_"):
        return "internal"
    return "public"


def _python_signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """Build a human-readable signature from an ast function node."""
    args = node.args
    parts: list[str] = []

    # Positional args
    for i, arg in enumerate(args.args):
        name = arg.arg
        annotation = ""
        if arg.annotation:
            with contextlib.suppress(Exception):
                annotation = f": {ast.unparse(arg.annotation)}"

        # Check for default value
        default_offset = len(args.args) - len(args.defaults)
        default = ""
        if i >= default_offset:
            with contextlib.suppress(Exception):
                default = f" = {ast.unparse(args.defaults[i - default_offset])}"

        parts.append(f"{name}{annotation}{default}")

    # *args
    if args.vararg:
        ann = ""
        if args.vararg.annotation:
            with contextlib.suppress(Exception):
                ann = f": {ast.unparse(args.vararg.annotation)}"
        parts.append(f"*{args.vararg.arg}{ann}")

    # **kwargs
    if args.kwarg:
        ann = ""
        if args.kwarg.annotation:
            with contextlib.suppress(Exception):
                ann = f": {ast.unparse(args.kwarg.annotation)}"
        parts.append(f"**{args.kwarg.arg}{ann}")

    sig = f"{node.name}({', '.join(parts)})"

    # Return annotation
    if node.returns:
        with contextlib.suppress(Exception):
            sig += f" -> {ast.unparse(node.returns)}"

    return sig


def _extract_source_lines(lines: list[str], start: int, end: int) -> str:
    """Extract source code from line range (1-indexed)."""
    extracted = lines[start - 1 : end]
    return textwrap.dedent("\n".join(extracted))


def _parse_python_function(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    lines: list[str],
    kind: str = "function",
) -> CodeSymbol:
    """Parse a Python function/method node into a CodeSymbol."""
    sig = _python_signature(node)
    if isinstance(node, ast.AsyncFunctionDef):
        sig = f"async {sig}"

    docstring = ast.get_docstring(node)
    source = _extract_source_lines(lines, node.lineno, node.end_lineno or node.lineno)

    return CodeSymbol(
        kind=kind,
        name=node.name,
        signature=sig,
        source_code=source,
        line_start=node.lineno,
        line_end=node.end_lineno or node.lineno,
        docstring=docstring,
        visibility=_python_visibility(node.name),
    )


def parse_python(source: Path) -> ParsedCodeFile:
    """Parse a Python file using the ast module.

    Extracts:
    - Module-level docstring
    - Import statements
    - Classes (with methods as children)
    - Top-level functions (sync and async)
    """
    try:
        content = source.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        logger.warning("[code_parser] Cannot read %s: %s", source, e)
        return parse_raw(source, "python")

    try:
        tree = ast.parse(content, filename=str(source))
    except SyntaxError as e:
        logger.warning("[code_parser] Syntax error in %s: %s", source, e)
        return parse_raw(source, "python")

    lines = content.splitlines()
    imports: list[str] = []
    symbols: list[CodeSymbol] = []

    # Module docstring
    file_docstring = ast.get_docstring(tree)

    for node in ast.iter_child_nodes(tree):
        # Imports
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name)

        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for alias in node.names:
                imports.append(f"{module}.{alias.name}")

        # Classes
        elif isinstance(node, ast.ClassDef):
            class_docstring = ast.get_docstring(node)
            class_source = _extract_source_lines(
                lines, node.lineno, node.end_lineno or node.lineno
            )

            # Extract methods
            children: list[CodeSymbol] = []
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    children.append(_parse_python_function(child, lines, kind="method"))

            symbols.append(
                CodeSymbol(
                    kind="class",
                    name=node.name,
                    signature=node.name,
                    source_code=class_source,
                    line_start=node.lineno,
                    line_end=node.end_lineno or node.lineno,
                    docstring=class_docstring,
                    children=children,
                    visibility=_python_visibility(node.name),
                )
            )

        # Top-level functions
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            symbols.append(_parse_python_function(node, lines))

    return ParsedCodeFile(
        filepath=source,
        language="python",
        imports=imports,
        symbols=symbols,
        file_docstring=file_docstring,
        parse_method="ast",
    )


# ---------------------------------------------------------------------------
# Tree-sitter parser
# ---------------------------------------------------------------------------


def _tree_sitter_extract_name(node: object) -> str:
    """Extract the name/identifier from a tree-sitter node."""
    # tree-sitter nodes have .children attribute
    for child in getattr(node, "children", []):
        node_type = getattr(child, "type", "")
        if node_type in (
            "identifier",
            "name",
            "type_identifier",
            "field_identifier",
        ):
            raw = getattr(child, "text", None) or b""
            return raw.decode("utf-8", errors="replace")
    # Fallback: first line up to opening paren or brace
    raw = getattr(node, "text", None) or b""
    text = raw.decode("utf-8", errors="replace")
    first_line = text.split("\n")[0].strip()
    return first_line[:80]


def _tree_sitter_extract_signature(node: object) -> str:
    """Extract the signature (first line / declaration) from a tree-sitter node."""
    raw = getattr(node, "text", None) or b""
    text = raw.decode("utf-8", errors="replace")
    first_line = text.split("\n")[0].strip()
    # Cap at 120 chars
    if len(first_line) > 120:
        first_line = first_line[:117] + "..."
    return first_line


def parse_with_tree_sitter(source: Path, language: str) -> ParsedCodeFile:
    """Parse a source file using tree-sitter.

    Requires tree-sitter-language-pack to be installed.
    Falls back to parse_raw() on any error.
    """
    if not HAS_TREE_SITTER:
        return parse_raw(source, language)

    try:
        import tree_sitter_language_pack as tslp

        parser = tslp.get_parser(language)  # type: ignore[arg-type]
    except Exception as e:
        logger.warning(
            "[code_parser] tree-sitter parser not available for %s: %s",
            language,
            e,
        )
        return parse_raw(source, language)

    try:
        content_bytes = source.read_bytes()
    except OSError as e:
        logger.warning("[code_parser] Cannot read %s: %s", source, e)
        return parse_raw(source, language)

    try:
        tree = parser.parse(content_bytes)
    except Exception as e:
        logger.warning("[code_parser] tree-sitter parse failed for %s: %s", source, e)
        return parse_raw(source, language)

    node_types = TREE_SITTER_NODE_TYPES.get(language, {})
    if not node_types:
        return parse_raw(source, language)

    # Build reverse lookup: node_type_name → symbol_kind
    type_to_kind: dict[str, str] = {}
    import_types: set[str] = set()
    for kind, type_names in node_types.items():
        for type_name in type_names:
            if kind == "import":
                import_types.add(type_name)
            else:
                type_to_kind[type_name] = kind

    imports: list[str] = []
    symbols: list[CodeSymbol] = []

    # Walk top-level children of the root node
    root = tree.root_node
    for child in root.children:
        child_type = child.type

        # Imports
        if child_type in import_types:
            import_text = (child.text or b"").decode("utf-8", errors="replace").strip()
            imports.append(import_text)
            continue

        # Symbols
        if child_type in type_to_kind:
            kind = type_to_kind[child_type]
            name = _tree_sitter_extract_name(child)
            signature = _tree_sitter_extract_signature(child)
            source_text = (child.text or b"").decode("utf-8", errors="replace")
            start_line = child.start_point[0] + 1  # tree-sitter is 0-indexed
            end_line = child.end_point[0] + 1

            # Extract child methods for classes/structs
            children: list[CodeSymbol] = []
            method_types = set()
            for mk, mt_list in node_types.items():
                if mk in ("function", "method"):
                    method_types.update(mt_list)

            if kind in ("class", "struct", "interface", "trait", "impl", "package"):
                for grandchild in child.children:
                    # Look in body/block nodes too
                    search_nodes = [grandchild]
                    if grandchild.type in ("class_body", "block", "declaration_list"):
                        search_nodes = list(grandchild.children)

                    for search_node in search_nodes:
                        if search_node.type in method_types:
                            m_name = _tree_sitter_extract_name(search_node)
                            m_sig = _tree_sitter_extract_signature(search_node)
                            m_src = (search_node.text or b"").decode(
                                "utf-8", errors="replace"
                            )
                            children.append(
                                CodeSymbol(
                                    kind="method",
                                    name=m_name,
                                    signature=m_sig,
                                    source_code=m_src,
                                    line_start=search_node.start_point[0] + 1,
                                    line_end=search_node.end_point[0] + 1,
                                )
                            )

            symbols.append(
                CodeSymbol(
                    kind=kind,
                    name=name,
                    signature=signature,
                    source_code=source_text,
                    line_start=start_line,
                    line_end=end_line,
                    children=children,
                )
            )

    return ParsedCodeFile(
        filepath=source,
        language=language,
        imports=imports,
        symbols=symbols,
        parse_method="tree-sitter",
    )


# ---------------------------------------------------------------------------
# Raw fallback parser
# ---------------------------------------------------------------------------


def parse_raw(source: Path, language: str) -> ParsedCodeFile:
    """Fallback parser — no structure extraction.

    Returns a ParsedCodeFile with no symbols. The converter will
    produce a code-fenced markdown block with the full file content.
    """
    return ParsedCodeFile(
        filepath=source,
        language=language,
        imports=[],
        symbols=[],
        parse_method="raw",
    )
