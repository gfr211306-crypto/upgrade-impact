"""Find dependency imports and direct calls without importing repository code.

Names are resolved from explicit imports in source order. Dynamic aliases,
star imports, and control-flow-dependent bindings are not inferred.
"""

import ast
from dataclasses import dataclass, field
import os
from pathlib import Path
import tokenize
from typing import Literal


SKIP_DIRS = frozenset(
    {".venv", "venv", ".git", "node_modules", "build", "dist", "site-packages"}
)


@dataclass(frozen=True)
class Usage:
    """An import or call, with a file path relative to the scanned repository.

    Call arguments describe only explicit syntax: ``*args`` are not counted
    and ``**kwargs`` do not contribute keyword names.
    """

    file: Path
    line: int
    path: str
    kind: Literal["import", "call"]
    keywords: frozenset[str] = frozenset()
    positional_args: int = 0


class _LocalNames(ast.NodeVisitor):
    """Collect local binders without leaking names from nested scopes."""

    def __init__(self) -> None:
        self.names: set[str] = set()
        self.external_names: set[str] = set()

    def visit_Global(self, node: ast.Global) -> None:
        self.external_names.update(node.names)

    visit_Nonlocal = visit_Global

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self.names.add(node.id)

    def visit_Import(self, node: ast.Import) -> None:
        self.names.update(alias.asname or alias.name.split(".")[0] for alias in node.names)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        self.names.update(alias.asname or alias.name for alias in node.names)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.names.add(node.name)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.names.add(node.name)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        pass

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.name:
            self.names.add(node.name)
        self.generic_visit(node)

    def visit_comprehension(self, node: ast.comprehension) -> None:
        # The target belongs to the comprehension, not the enclosing function.
        self.visit(node.iter)
        for condition in node.ifs:
            self.visit(condition)


@dataclass
class _Scope:
    kind: Literal["module", "function", "class"]
    names: dict[str, str | None] = field(default_factory=dict)


class _Scanner(ast.NodeVisitor):
    def __init__(self, file: Path, import_name: str) -> None:
        self.file = file
        self.import_name = import_name
        self.scopes = [_Scope("module")]
        self.usages: list[tuple[int, int, Usage]] = []

    def _record(self, node: ast.AST, path: str, kind: Literal["import", "call"]) -> None:
        if path != self.import_name and not path.startswith(self.import_name + "."):
            return
        keywords = frozenset()
        positional_args = 0
        if isinstance(node, ast.Call):
            keywords = frozenset(keyword.arg for keyword in node.keywords if keyword.arg)
            positional_args = sum(not isinstance(arg, ast.Starred) for arg in node.args)
        usage = Usage(self.file, node.lineno, path, kind, keywords, positional_args)
        self.usages.append((node.lineno, node.col_offset, usage))

    def _resolve(self, node: ast.expr) -> str | None:
        if isinstance(node, ast.Attribute):
            parent = self._resolve(node.value)
            return f"{parent}.{node.attr}" if parent else None
        if isinstance(node, ast.Name):
            crossed_scope = False
            for scope in reversed(self.scopes):
                # Classes do not enclose methods or nested class bodies.
                if scope.kind == "class" and crossed_scope:
                    continue
                if node.id in scope.names:
                    return scope.names[node.id]
                crossed_scope |= scope.kind in {"function", "class"}
        return None

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            name = alias.asname or alias.name.split(".")[0]
            self.scopes[-1].names[name] = alias.name if alias.asname else name
            self._record(alias, alias.name, "import")

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            if alias.name == "*":
                if node.level == 0 and node.module:
                    self._record(node, node.module, "import")
                continue
            path = f"{node.module}.{alias.name}" if node.level == 0 and node.module else None
            self.scopes[-1].names[alias.asname or alias.name] = path
            if path:
                self._record(alias, path, "import")

    def visit_Call(self, node: ast.Call) -> None:
        path = self._resolve(node.func)
        if path:
            self._record(node, path, "call")
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self.scopes[-1].names[node.id] = None

    def visit_Assign(self, node: ast.Assign) -> None:
        self.visit(node.value)
        for target in node.targets:
            self.visit(target)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self.visit(node.annotation)
        if node.value:
            self.visit(node.value)
            self.visit(node.target)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        self.visit(node.value)
        self.visit(node.target)

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        self.visit(node.value)
        self.visit(node.target)

    def visit_For(self, node: ast.For) -> None:
        self.visit(node.iter)
        self.visit(node.target)
        for statement in [*node.body, *node.orelse]:
            self.visit(statement)

    visit_AsyncFor = visit_For

    def _function_scope(self, args: ast.arguments, body: list[ast.stmt]) -> _Scope:
        local_names = _LocalNames()
        for statement in body:
            local_names.visit(statement)
        parameters = [*args.posonlyargs, *args.args, *args.kwonlyargs]
        if args.vararg:
            parameters.append(args.vararg)
        if args.kwarg:
            parameters.append(args.kwarg)
        local_names.names.update(parameter.arg for parameter in parameters)
        return _Scope(
            "function", dict.fromkeys(local_names.names - local_names.external_names)
        )

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        for decorator in node.decorator_list:
            self.visit(decorator)
        self.visit(node.args)
        if node.returns:
            self.visit(node.returns)
        self.scopes[-1].names[node.name] = None
        self.scopes.append(self._function_scope(node.args, node.body))
        for statement in node.body:
            self.visit(statement)
        self.scopes.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self.visit(node.args)
        self.scopes.append(self._function_scope(node.args, []))
        self.visit(node.body)
        self.scopes.pop()

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        for expression in [*node.decorator_list, *node.bases, *node.keywords]:
            self.visit(expression)
        self.scopes.append(_Scope("class"))
        for statement in node.body:
            self.visit(statement)
        self.scopes.pop()
        self.scopes[-1].names[node.name] = None

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.type:
            self.visit(node.type)
        if node.name:
            self.scopes[-1].names[node.name] = None
        for statement in node.body:
            self.visit(statement)

    def _comprehension(self, node: ast.AST) -> None:
        self.visit(node.generators[0].iter)
        local_names = _LocalNames()
        for generator in node.generators:
            local_names.visit(generator.target)
        self.scopes.append(_Scope("function", dict.fromkeys(local_names.names)))
        for index, generator in enumerate(node.generators):
            if index:
                self.visit(generator.iter)
            self.visit(generator.target)
            for condition in generator.ifs:
                self.visit(condition)
        if isinstance(node, ast.DictComp):
            self.visit(node.key)
            self.visit(node.value)
        else:
            self.visit(node.elt)
        self.scopes.pop()

    visit_ListComp = _comprehension
    visit_SetComp = _comprehension
    visit_DictComp = _comprehension
    visit_GeneratorExp = _comprehension


def scan(repo_path: str | Path, import_name: str) -> list[Usage]:
    """Scan Python files for explicit imports and calls into ``import_name``.

    Unparseable files and excluded directories are skipped. Files are decoded
    according to Python's encoding-cookie rules. I/O errors propagate to the
    caller. Results are ordered by relative file path, line, and column.
    """
    root = Path(repo_path)
    if not root.is_dir():
        raise NotADirectoryError(root)

    def raise_walk_error(error: OSError) -> None:
        raise error

    files = []
    for directory, dirs, names in os.walk(root, onerror=raise_walk_error):
        dirs[:] = [name for name in dirs if name not in SKIP_DIRS]
        files.extend(Path(directory) / name for name in names if name.endswith(".py"))

    usages = []
    for file in sorted(files, key=lambda path: path.relative_to(root).as_posix()):
        try:
            with tokenize.open(file) as source:
                tree = ast.parse(source.read(), filename=str(file))
        except (SyntaxError, UnicodeError):
            continue
        scanner = _Scanner(file.relative_to(root), import_name)
        scanner.visit(tree)
        usages.extend(usage for _, _, usage in sorted(scanner.usages, key=lambda item: item[:2]))
    return usages
