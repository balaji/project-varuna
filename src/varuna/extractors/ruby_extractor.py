"""Tree-sitter AST extractor for Ruby/Rails, including Zeitwerk constant resolution, associations, and routes."""

from __future__ import annotations

import re
from pathlib import Path
from typing import ClassVar

import tree_sitter_ruby as tsruby
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

RUBY_BUILTIN_CONSTANTS: set[str] = {
    "String",
    "Integer",
    "Float",
    "Array",
    "Hash",
    "Symbol",
    "Set",
    "Time",
    "Date",
    "DateTime",
    "File",
    "Dir",
    "IO",
    "ENV",
    "Kernel",
    "Object",
    "BasicObject",
    "Class",
    "Module",
    "StandardError",
    "RuntimeError",
    "ArgumentError",
    "Rails",
    "ActiveRecord",
    "ApplicationRecord",
    "ApplicationController",
    "ActionController",
    "ApplicationJob",
    "ActiveJob",
}


class RubyASTExtractor(BaseASTExtractor):
    """
    Extracts Ruby/Rails architectural facts:
    - Explicit `require` / `require_relative`
    - Implicit Zeitwerk constant references (`Billing::InvoiceService`, `User`)
    - Mixins (`include`, `extend`, `prepend`) and inheritance (`< ApplicationController`)
    - ActiveRecord associations (`belongs_to`, `has_many`, `has_one`)
    - Rails `config/routes.rb` HTTP route definitions & Sidekiq workers
    """

    language: ClassVar[Language] = Language.RUBY
    extensions: ClassVar[set[str]] = {".rb", ".rake"}

    def __init__(self) -> None:
        self._ts_lang = TSLanguage(tsruby.language())
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
            name=file_path.stem,
            file_path=str(file_path.resolve()),
            relative_path=rel_posix,
            language=Language.RUBY,
            directory_namespace=namespace,
            lines_of_code=loc,
        )

        defined_constants: set[str] = set()
        is_routes_file = rel_posix.endswith("routes.rb")

        for ts_node in walk_tree(root):
            ntype = ts_node.type

            if ntype in ("class", "module"):
                name_node = ts_node.child_by_field_name("name")
                if name_node:
                    const_name = node_text(name_node, source_bytes).strip()
                    defined_constants.add(const_name)
                    defined_constants.add(const_name.split("::")[-1])
                    node_model.classes.append(const_name)
                    node_model.exported_symbols.append(const_name)

                superclass_node = ts_node.child_by_field_name("superclass")
                if superclass_node:
                    sup_text = node_text(superclass_node, source_bytes).lstrip("<").strip()
                    if sup_text:
                        node_model.mixins_and_parents.append(sup_text)
                        if "Controller" in sup_text:
                            node_model.entrypoints.append(
                                EntrypointInfo(
                                    kind=EntrypointKind.HTTP_ROUTE,
                                    name=f"controller:{node_model.name}",
                                    detail=f"Rails controller ({sup_text})",
                                )
                            )
                        elif "Job" in sup_text or "Worker" in sup_text:
                            node_model.entrypoints.append(
                                EntrypointInfo(
                                    kind=EntrypointKind.BACKGROUND_WORKER,
                                    name=f"worker:{node_model.name}",
                                    detail=f"Background worker ({sup_text})",
                                )
                            )

            elif ntype == "method":
                name_node = ts_node.child_by_field_name("name")
                if name_node:
                    m_name = node_text(name_node, source_bytes)
                    node_model.functions.append(m_name)
                    node_model.exported_symbols.append(m_name)

            elif ntype in ("scope_resolution", "constant"):
                const_ref = node_text(ts_node, source_bytes).strip().removeprefix("::")
                base_const = const_ref.split("::")[0]
                if (
                    const_ref
                    and base_const not in RUBY_BUILTIN_CONSTANTS
                    and const_ref not in RUBY_BUILTIN_CONSTANTS
                    and const_ref not in defined_constants
                    and not const_ref.isupper()
                ):
                    node_model.referenced_constants.append(const_ref)

            elif ntype == "call":
                self._inspect_ruby_call(ts_node, source_bytes, node_model, is_routes_file)

        # Deduplicate while preserving order
        node_model.referenced_constants = list(dict.fromkeys(node_model.referenced_constants))
        return node_model

    def _inspect_ruby_call(
        self, call_node, source_bytes: bytes, node_model: CodeNode, is_routes_file: bool
    ) -> None:
        method_node = call_node.child_by_field_name("method")
        args_node = call_node.child_by_field_name("arguments")
        if not method_node:
            return

        method_name = node_text(method_node, source_bytes).strip()
        args_text = node_text(args_node, source_bytes).strip() if args_node else ""

        # 1. Explicit require / require_relative
        if method_name in ("require", "require_relative") and args_text:
            req_match = re.search(r"['\"]([^'\"]+)['\"]", args_text)
            if req_match:
                node_model.raw_imports.append(req_match.group(1))
            return

        # 2. Mixins: include / extend / prepend (e.g., include Sidekiq::Worker, include Authenticatable)
        if method_name in ("include", "extend", "prepend") and args_text:
            mixin_name = args_text.strip("() ")
            node_model.mixins_and_parents.append(mixin_name)
            node_model.referenced_constants.append(mixin_name)
            if "Sidekiq::Worker" in mixin_name or "Sidekiq::Job" in mixin_name:
                node_model.entrypoints.append(
                    EntrypointInfo(
                        kind=EntrypointKind.BACKGROUND_WORKER,
                        name=f"sidekiq:{node_model.name}",
                        detail="Sidekiq Worker",
                    )
                )
            return

        # 3. ActiveRecord associations: belongs_to :account, has_many :invoices
        if (
            method_name in ("belongs_to", "has_many", "has_one", "has_and_belongs_to_many")
            and args_text
        ):
            sym_match = re.search(r":([a-zA-Z0-9_]+)", args_text)
            if sym_match:
                assoc_target = sym_match.group(1)
                node_model.associations.append(assoc_target)
            return

        # 4. Rails routes.rb DSL (`resources :orders`, `get '/health', to: 'health#show'`)
        if is_routes_file or "routes" in node_model.name:
            if method_name in ("resources", "resource") and args_text:
                res_match = re.search(r":([a-zA-Z0-9_]+)", args_text)
                if res_match:
                    res_name = res_match.group(1)
                    node_model.routes.append(
                        RouteEndpoint(
                            method="REST",
                            path=f"/{res_name}",
                            handler_symbol=f"{res_name}_controller",
                            source_file=node_model.relative_path,
                        )
                    )
                    node_model.referenced_constants.append(f"{res_name}_controller")
            elif method_name in ("get", "post", "put", "patch", "delete") and args_text:
                path_match = re.search(r"['\"]([^'\"]+)['\"]", args_text)
                to_match = re.search(r"([a-z0-9_]+)#([a-z0-9_]+)", args_text)
                r_path = path_match.group(1) if path_match else "/"
                handler = f"{to_match.group(1)}_controller" if to_match else ""
                node_model.routes.append(
                    RouteEndpoint(
                        method=method_name.upper(),
                        path=r_path,
                        handler_symbol=handler,
                        source_file=node_model.relative_path,
                    )
                )
                if handler:
                    node_model.referenced_constants.append(handler)
            return

        # 5. General method call tracking
        receiver_node = call_node.child_by_field_name("receiver")
        if receiver_node:
            recv_text = node_text(receiver_node, source_bytes).strip()
            node_model.called_symbols.append(f"{recv_text}.{method_name}")
        else:
            node_model.called_symbols.append(method_name)
