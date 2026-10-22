"""Tree-sitter AST extractor for TypeScript/JavaScript, Express/Fastify routes, and NestJS decorators."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Set
import tree_sitter_typescript as tstypescript
from tree_sitter import Language as TSLanguage, Parser

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


class TypeScriptASTExtractor(BaseASTExtractor):
    """Extracts ESM/CJS imports, classes, functions, Express routes, and NestJS decorators from TS/JS files."""

    language = Language.TYPESCRIPT
    extensions: Set[str] = {".ts", ".tsx", ".js", ".jsx", ".mjs"}

    def __init__(self) -> None:
        self._ts_lang = TSLanguage(tstypescript.language_typescript())
        self._tsx_lang = TSLanguage(tstypescript.language_tsx())
        self._ts_parser = Parser(self._ts_lang)
        self._tsx_parser = Parser(self._tsx_lang)

    def extract_file(self, repo_root: Path, file_path: Path) -> CodeNode:
        source_bytes = file_path.read_bytes()
        parser = self._tsx_parser if file_path.suffix.lower() in (".tsx", ".jsx") else self._ts_parser
        tree = parser.parse(source_bytes)
        root = tree.root_node

        rel_posix = file_path.relative_to(repo_root).as_posix()
        mod_id = compute_module_id(repo_root, file_path)
        namespace = infer_directory_namespace(repo_root, file_path)
        loc = len(source_bytes.splitlines())

        node_model = CodeNode(
            id=mod_id,
            name=file_path.stem if file_path.stem != "index" else file_path.parent.name,
            file_path=str(file_path.resolve()),
            relative_path=rel_posix,
            language=Language.TYPESCRIPT,
            directory_namespace=namespace,
            lines_of_code=loc,
        )

        controller_prefix = ""

        for ts_node in walk_tree(root):
            ntype = ts_node.type

            if ntype in ("import_statement", "export_statement"):
                source_node = ts_node.child_by_field_name("source")
                if source_node:
                    imp_str = node_text(source_node, source_bytes).strip("\"'`")
                    if imp_str:
                        node_model.raw_imports.append(imp_str)

                # Capture named imported/exported identifiers
                stmt_text = node_text(ts_node, source_bytes)
                for ident in re.findall(r"\{\s*([^}]+)\s*\}", stmt_text):
                    for part in ident.split(","):
                        clean_sym = part.strip().split(" as ")[0].strip()
                        if clean_sym and clean_sym.isidentifier():
                            node_model.referenced_constants.append(clean_sym)

            elif ntype in ("class_declaration", "interface_declaration", "type_alias_declaration"):
                name_node = ts_node.child_by_field_name("name")
                if name_node:
                    cls_name = node_text(name_node, source_bytes)
                    node_model.classes.append(cls_name)
                    node_model.exported_symbols.append(cls_name)

            elif ntype in ("function_declaration", "method_definition"):
                name_node = ts_node.child_by_field_name("name")
                if name_node:
                    fn_name = node_text(name_node, source_bytes)
                    node_model.functions.append(fn_name)
                    node_model.exported_symbols.append(fn_name)

            elif ntype == "decorator":
                dec_text = node_text(ts_node, source_bytes).lstrip("@").strip()
                # NestJS @Controller('/users')
                ctrl_match = re.match(r"^Controller\(\s*['\"`]?([^'\"`)]*)['\"`]?\s*\)", dec_text)
                if ctrl_match:
                    controller_prefix = "/" + ctrl_match.group(1).strip("/")
                    continue
                # NestJS @Get('/profile'), @Post()
                verb_match = re.match(
                    r"^(Get|Post|Put|Delete|Patch)\(\s*['\"`]?([^'\"`)]*)['\"`]?\s*\)",
                    dec_text,
                )
                if verb_match:
                    verb = verb_match.group(1).upper()
                    sub_path = verb_match.group(2).strip("/")
                    full_path = f"{controller_prefix}/{sub_path}".rstrip("/") or "/"
                    node_model.routes.append(
                        RouteEndpoint(
                            method=verb,
                            path=full_path,
                            handler_symbol=node_model.name,
                            source_file=node_model.relative_path,
                        )
                    )
                    node_model.entrypoints.append(
                        EntrypointInfo(
                            kind=EntrypointKind.HTTP_ROUTE,
                            name=f"{verb} {full_path}",
                            detail=f"NestJS endpoint in {node_model.name}",
                        )
                    )

            elif ntype == "call_expression":
                fn_node = ts_node.child_by_field_name("function")
                args_node = ts_node.child_by_field_name("arguments")
                if not fn_node:
                    continue
                fn_str = node_text(fn_node, source_bytes).strip()
                node_model.called_symbols.append(fn_str)

                # CommonJS require('...')
                if fn_str == "require" and args_node:
                    arg_text = node_text(args_node, source_bytes)
                    req_match = re.search(r"['\"`]([^'\"`]+)['\"`]", arg_text)
                    if req_match:
                        node_model.raw_imports.append(req_match.group(1))

                # Express / Fastify router.get('/orders', handler)
                express_match = re.match(r"^([A-Za-z0-9_$]+)\.(get|post|put|delete|patch)\b", fn_str)
                if express_match and args_node:
                    caller_obj = express_match.group(1).lower()
                    if caller_obj in ("app", "router", "server", "fastify", "api"):
                        verb = express_match.group(2).upper()
                        arg_text = node_text(args_node, source_bytes)
                        path_match = re.search(r"^\(\s*['\"`](/[^'\"`]*)['\"`]", arg_text)
                        if path_match:
                            r_path = path_match.group(1)
                            node_model.routes.append(
                                RouteEndpoint(
                                    method=verb,
                                    path=r_path,
                                    handler_symbol=node_model.name,
                                    source_file=node_model.relative_path,
                                )
                            )
                            node_model.entrypoints.append(
                                EntrypointInfo(
                                    kind=EntrypointKind.HTTP_ROUTE,
                                    name=f"{verb} {r_path}",
                                    detail=f"Express/Fastify route in {node_model.name}",
                                )
                            )

            elif ntype == "new_expression":
                ctor_node = ts_node.child_by_field_name("constructor")
                if ctor_node:
                    ctor_name = node_text(ctor_node, source_bytes).strip()
                    node_model.referenced_constants.append(ctor_name)
                    if ctor_name in ("Worker", "QueueScheduler"):
                        node_model.entrypoints.append(
                            EntrypointInfo(
                                kind=EntrypointKind.BACKGROUND_WORKER,
                                name=f"worker:{node_model.name}",
                                detail="BullMQ/Node Worker processor",
                            )
                        )

        return node_model
