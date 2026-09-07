import ast
import os
import json
from typing import List, Dict, Optional, Any
from pydantic import BaseModel, Field

class CodeSymbol(BaseModel):
    name: str
    kind: str  # "class", "function", "async_function"
    file_path: str
    line_start: int
    line_end: int
    docstring: Optional[str] = None

class FileEntry(BaseModel):
    path: str
    mtime: float
    size: int
    symbols: List[CodeSymbol] = Field(default_factory=list)

class CodeIndex:
    IGNORE_DIRS = {
        ".git",
        "__pycache__",
        ".pytest_cache",
        ".state",
        "live_logs",
        "node_modules",
        ".gemini",
        ".agents",
    }

    def __init__(self, root_dir: str = ".", cache_file: str = ".state/code_index.json"):
        self.root_dir = root_dir
        self.cache_file = cache_file
        self.files: Dict[str, FileEntry] = {}
        self.symbol_map: Dict[str, List[CodeSymbol]] = {}
        self._content_cache: Dict[str, tuple[float, List[str]]] = {}
        self._load_cache()

    def _load_cache(self) -> None:
        if os.path.exists(self.cache_file):
            try:
                with open(self.cache_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for path, entry_data in data.get("files", {}).items():
                    entry = FileEntry.model_validate(entry_data)
                    self.files[path] = entry
                    for sym in entry.symbols:
                        self.symbol_map.setdefault(sym.name, []).append(sym)
            except Exception:
                self.files = {}
                self.symbol_map = {}

    def _save_cache(self) -> None:
        try:
            os.makedirs(os.path.dirname(os.path.abspath(self.cache_file)), exist_ok=True)
            data = {
                "files": {k: v.model_dump() for k, v in self.files.items()}
            }
            tmp_file = self.cache_file + ".tmp"
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp_file, self.cache_file)
        except Exception:
            pass

    def _parse_py_file(self, file_path: str) -> List[CodeSymbol]:
        symbols: List[CodeSymbol] = []
        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                code = f.read()
            tree = ast.parse(code, filename=file_path)
            for node in ast.iter_child_nodes(tree):
                if isinstance(node, ast.ClassDef):
                    doc = ast.get_docstring(node)
                    end_lineno = getattr(node, "end_lineno", node.lineno)
                    symbols.append(CodeSymbol(
                        name=node.name,
                        kind="class",
                        file_path=file_path,
                        line_start=node.lineno,
                        line_end=end_lineno,
                        docstring=doc,
                    ))
                    for item in node.body:
                        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            m_doc = ast.get_docstring(item)
                            m_end = getattr(item, "end_lineno", item.lineno)
                            symbols.append(CodeSymbol(
                                name=f"{node.name}.{item.name}",
                                kind="method",
                                file_path=file_path,
                                line_start=item.lineno,
                                line_end=m_end,
                                docstring=m_doc,
                            ))
                elif isinstance(node, ast.AsyncFunctionDef):
                    doc = ast.get_docstring(node)
                    end_lineno = getattr(node, "end_lineno", node.lineno)
                    symbols.append(CodeSymbol(
                        name=node.name,
                        kind="async_function",
                        file_path=file_path,
                        line_start=node.lineno,
                        line_end=end_lineno,
                        docstring=doc,
                    ))
                elif isinstance(node, ast.FunctionDef):
                    doc = ast.get_docstring(node)
                    end_lineno = getattr(node, "end_lineno", node.lineno)
                    symbols.append(CodeSymbol(
                        name=node.name,
                        kind="function",
                        file_path=file_path,
                        line_start=node.lineno,
                        line_end=end_lineno,
                        docstring=doc,
                    ))
        except Exception:
            pass
        return symbols

    def build_or_update(self) -> Dict[str, int]:
        total_files = 0
        parsed_files = 0
        cache_hits = 0
        current_seen: set[str] = set()

        for root, dirs, files in os.walk(self.root_dir):
            dirs[:] = [d for d in dirs if d not in self.IGNORE_DIRS]
            for file in files:
                if not file.endswith(".py"):
                    continue
                file_path = os.path.normpath(os.path.join(root, file))
                current_seen.add(file_path)
                total_files += 1

                try:
                    stat = os.stat(file_path)
                except OSError:
                    continue

                if file_path in self.files and self.files[file_path].mtime == stat.st_mtime:
                    cache_hits += 1
                else:
                    parsed_files += 1
                    symbols = self._parse_py_file(file_path)
                    self.files[file_path] = FileEntry(
                        path=file_path,
                        mtime=stat.st_mtime,
                        size=stat.st_size,
                        symbols=symbols,
                    )

        # Remove deleted files
        for old_path in list(self.files.keys()):
            if old_path not in current_seen:
                del self.files[old_path]

        # Rebuild symbol map
        self.symbol_map.clear()
        for entry in self.files.values():
            for sym in entry.symbols:
                self.symbol_map.setdefault(sym.name, []).append(sym)

        self._save_cache()
        return {
            "total_files": total_files,
            "parsed_files": parsed_files,
            "cache_hits": cache_hits,
        }

    def find_symbol(self, name: str, exact: bool = True) -> List[CodeSymbol]:
        if exact:
            return self.symbol_map.get(name, [])
        matches: List[CodeSymbol] = []
        name_lower = name.lower()
        for sym_name, sym_list in self.symbol_map.items():
            if name_lower in sym_name.lower():
                matches.extend(sym_list)
        return matches

    def get_file_outline(self, file_path: str) -> List[CodeSymbol]:
        norm = os.path.normpath(file_path)
        for path, entry in self.files.items():
            if norm == path or norm.endswith(path) or path.endswith(norm):
                return entry.symbols
        return []

    def get_cached_chunk(self, file_path: str, start_line: int = 1, end_line: int = 100) -> str:
        norm = os.path.normpath(file_path)
        try:
            stat = os.stat(norm)
            mtime = stat.st_mtime
        except OSError:
            return f"Error: File '{file_path}' not found."

        cached = self._content_cache.get(norm)
        if cached is None or cached[0] != mtime:
            try:
                with open(norm, "r", encoding="utf-8", errors="replace") as f:
                    lines = f.readlines()
                self._content_cache[norm] = (mtime, lines)
            except Exception as e:
                return f"Error reading file '{file_path}': {e}"
        else:
            lines = cached[1]

        total = len(lines)
        s = max(1, start_line) - 1
        e = min(total, end_line)
        slice_lines = lines[s:e]
        return f"[{norm} | Lines {s+1}-{e} of {total}]\n" + "".join(slice_lines)

    def get_sitemap(self) -> Dict[str, Any]:
        sitemap = {}
        for path, entry in sorted(self.files.items()):
            sitemap[path] = [
                {"name": s.name, "kind": s.kind, "lines": f"{s.line_start}-{s.line_end}"}
                for s in entry.symbols
            ]
        return sitemap

_GLOBAL_INDEX: Optional[CodeIndex] = None

def get_code_index(root_dir: str = ".") -> CodeIndex:
    global _GLOBAL_INDEX
    if _GLOBAL_INDEX is None:
        _GLOBAL_INDEX = CodeIndex(root_dir=root_dir)
        _GLOBAL_INDEX.build_or_update()
    return _GLOBAL_INDEX
