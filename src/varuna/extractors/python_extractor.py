"""Tree-sitter AST extractor for Python source code, FastAPI/Flask/Django routes, and Typer/Click CLIs."""

from __future__ import annotations

import re
from pathlib import Path
from typing import ClassVar

import tree_sitter_python as tspython
from tree_sitter import Language as TSLanguage
from tree_sitter import Parser

from varuna.extractors.base import (
    BaseASTExtractor,
    compute_module_id,
    infer_directory_namespace,
    node_text,
    walk_tree,
)
from varuna.models.graph_schema import (
    CodeNode,
    EntrypointInfo,
    EntrypointKind,
    Language,
    RouteEndpoint,
)

HTTP_VERBS: set[str] = {
    "get",
    "post",
    "put",
    "delete",
    "patch",
    "options",
    "head",
    "route",
    "api_route",
}
CLI_DECORATORS: set[str] = {"command", "group"}
WORKER_DECORATORS: set[str] = {"task", "shared_task", "job"}


class PythonASTExtractor(BaseASTExtractor):
    """Extracts imports, classes, functions, call sites, HTTP routes, and CLI entrypoints from Python files."""

    language: ClassVar[Language] = Language.PYTHON
    extensions: ClassVar[set[str]] = {".py"}

    def __init__(self) -> None:
        self._ts_lang = TSLanguage(tspython.language())
        self._parser = Parser(self._ts_lang)

    def extract_file(self, repo_root: Path, file_path: Path) -> CodeNode:
        source_bytes = file_path.read_bytes()
        tree = self._parser.parse(source_bytes)
        root = tree.root_node

        rel_posix = file_path.relative_to(repo_root).as_posix()
        mod_id = compute_module_id(repo_root, file_path)
        namespace = infer_directory_namespace(repo_root, file_path)
        loc = len(source_bytes.splitlines())

        node_model = CodeNode(
            id=mod_id,
            name=file_path.stem if file_path.stem != "__init__" else file_path.parent.name,
            file_path=str(file_path.resolve()),
            relative_path=rel_posix,
            language=Language.PYTHON,
            directory_namespace=namespace,
            lines_of_code=loc,
        )

        # Extract module docstring if first statement is an expression_statement with a string
        if root.children:
            first_stmt = root.children[0]
            if first_stmt.type == "expression_statement" and first_stmt.children:
                expr = first_stmt.children[0]
                if expr.type == "string":
                    raw_doc = node_text(expr, source_bytes).strip("\"' \n\t")
                    node_model.docstring = raw_doc.splitlines()[0] if raw_doc else ""

        for ts_node in walk_tree(root):
            ntype = ts_node.type

            if ntype == "import_statement":
                # e.g., import networkx as nx, os.path
                for child in ts_node.children:
                    if child.type == "dotted_name":
                        imp = node_text(child, source_bytes).strip()
                        node_model.raw_imports.append(imp)
                    elif child.type == "aliased_import":
                        name_node = child.child_by_field_name("name")
                        if name_node:
                            node_model.raw_imports.append(
                                node_text(name_node, source_bytes).strip()
                            )

            elif ntype == "import_from_statement":
                # e.g., from varuna.models.graph_schema import CodeNode, AnalysisResult
                module_name_node = ts_node.child_by_field_name("module_name")
                base_mod = (
                    node_text(module_name_node, source_bytes).strip() if module_name_node else ""
                )
                if base_mod:
                    node_model.raw_imports.append(base_mod)
                # Also record imported symbol names to strengthen coupling weights and resolve submodules
                for child in ts_node.children:
                    if child.type == "dotted_name" and child != module_name_node:
                        sym = node_text(child, source_bytes).strip()
                        if base_mod:
                            node_model.raw_imports.append(f"{base_mod}.{sym}")
                        node_model.referenced_constants.append(sym)
                    elif child.type == "aliased_import":
                        orig = child.child_by_field_name("name")
                        if orig:
                            sym = node_text(orig, source_bytes).strip()
                            if base_mod:
                                node_model.raw_imports.append(f"{base_mod}.{sym}")
                            node_model.referenced_constants.append(sym)

            elif ntype == "class_definition":
                name_node = ts_node.child_by_field_name("name")
                if name_node:
                    cls_name = node_text(name_node, source_bytes)
                    node_model.classes.append(cls_name)
                    if not cls_name.startswith("_"):
                        node_model.exported_symbols.append(cls_name)
                superclasses = ts_node.child_by_field_name("superclasses")
                if superclasses:
                    for arg in superclasses.children:
                        if arg.type in ("identifier", "attribute"):
                            parent_name = node_text(arg, source_bytes)
                            node_model.mixins_and_parents.append(parent_name)

            elif ntype == "function_definition":
                name_node = ts_node.child_by_field_name("name")
                if name_node:
                    fn_name = node_text(name_node, source_bytes)
                    node_model.functions.append(fn_name)
                    if not fn_name.startswith("_"):
                        node_model.exported_symbols.append(fn_name)

            elif ntype == "decorated_definition":
                self._inspect_decorated_definition(ts_node, source_bytes, node_model)

            elif ntype == "call":
                fn_node = ts_node.child_by_field_name("function")
                if fn_node:
                    called = node_text(fn_node, source_bytes).strip()
                    if called and len(called) < 80:
                        node_model.called_symbols.append(called)

            elif ntype == "if_statement":
                cond = ts_node.child_by_field_name("condition")
                if cond and re.match(
                    r"^__name__\s*==\s*['\"]__main__['\"]$",
                    node_text(cond, source_bytes).strip(),
                ):
                    node_model.entrypoints.append(
                        EntrypointInfo(
                            kind=EntrypointKind.CLI_COMMAND,
                            name=f"python -m {mod_id}",
                            detail="Main module execution block",
                        )
                    )

        return node_model

    def _inspect_decorated_definition(
        self, decorated_node, source_bytes: bytes, node_model: CodeNode
    ) -> None:
        """Detect FastAPI/Flask routes, Typer/Click CLI commands, and Celery tasks from decorators."""
        definition = decorated_node.child_by_field_name("definition")
        fn_name = ""
        if definition:
            name_node = definition.child_by_field_name("name")
            if name_node:
                fn_name = node_text(name_node, source_bytes)

        for child in decorated_node.children:
            if child.type != "decorator":
                continue
            dec_text = node_text(child, source_bytes).lstrip("@").strip()

            # Match @app.get("/path") / @router.post("/items") / @bp.route("/x")
            route_match = re.match(
                r"^([A-Za-z0-9_]+)\.(get|post|put|delete|patch|options|head|route|api_route)\s*\((.*)\)",
                dec_text,
                re.DOTALL,
            )
            if route_match:
                verb = route_match.group(2).upper()
                if verb in ("ROUTE", "API_ROUTE"):
                    verb = "HTTP"
                args_str = route_match.group(3)
                path_match = re.search(r"['\"]([^'\"]+)['\"]", args_str)
                route_path = path_match.group(1) if path_match else f"/{fn_name}"
                node_model.routes.append(
                    RouteEndpoint(
                        method=verb,
                        path=route_path,
                        handler_symbol=fn_name,
                        source_file=node_model.relative_path,
                    )
                )
                node_model.entrypoints.append(
                    EntrypointInfo(
                        kind=EntrypointKind.HTTP_ROUTE,
                        name=f"{verb} {route_path}",
                        detail=f"Handler: {fn_name}",
                    )
                )
                continue

            # Match Typer/Click @app.command() or @cli.command("name")
            cli_match = re.match(r"^([A-Za-z0-9_]+)\.(command|group)\b", dec_text)
            if cli_match or dec_text.startswith("command("):
                cmd_name = fn_name.replace("_", "-")
                str_arg = re.search(r"^\w+\.\w+\(\s*['\"]([^'\"]+)['\"]", dec_text)
                if str_arg:
                    cmd_name = str_arg.group(1)
                node_model.entrypoints.append(
                    EntrypointInfo(
                        kind=EntrypointKind.CLI_COMMAND,
                        name=f"cli:{cmd_name}",
                        detail=f"CLI command handler `{fn_name}`",
                    )
                )
                continue

            # Match Celery / RQ background worker decorators (@app.task, @shared_task)
            worker_match = re.search(r"\b(task|shared_task|job)\b", dec_text)
            if worker_match:
                node_model.entrypoints.append(
                    EntrypointInfo(
                        kind=EntrypointKind.BACKGROUND_WORKER,
                        name=f"worker:{fn_name}",
                        detail=f"Async task `{fn_name}`",
                    )
                )
