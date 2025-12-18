"""Локальний boundary-check для меж модулів та анти-`utils.py` рейки.

Ціль (B1): зупиняти хаотичні залежності в репо *до* CI і без масових рефакторів.

Правила — в `tools/import_rules.toml`.
Запуск: `python tools/check_import_boundaries.py`
"""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

# R2: pre-commit під Windows може запускатися не тим Python, що активний у venv.
# Тому робимо прозорий fallback: якщо немає stdlib `tomllib` (Python <3.11),
# використовуємо `tomli` (додається як additional_dependency у pre-commit hook).
try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore[import-not-found]


@dataclass(frozen=True, slots=True)
class ImportViolation:
    file: str
    lineno: int
    message: str


def _first_line_number(text: str, needle: str) -> int:
    idx = text.find(needle)
    if idx < 0:
        return 1
    return text.count("\n", 0, idx) + 1


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _load_rules(config_path: Path) -> dict[str, Any]:
    data = tomllib.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Некоректний формат конфігу import_rules.toml")
    return data


def _norm_rel(path: Path, *, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _iter_python_files(root: Path, *, ignore_dirs: set[str]) -> list[Path]:
    py_files: list[Path] = []

    def _is_ignored_dir(dir_path: Path) -> bool:
        return any(part in ignore_dirs for part in dir_path.parts)

    for path in root.rglob("*.py"):
        if _is_ignored_dir(path.parent):
            continue
        py_files.append(path)

    return py_files


def _top_level_package(file_path: Path, *, root: Path) -> str | None:
    try:
        rel = file_path.relative_to(root)
    except Exception:
        return None

    parts = rel.parts
    if not parts:
        return None

    # tools/* ми вважаємо інструментами, не бізнес-пакетом.
    if parts[0] == "tools":
        return None

    return parts[0]


def _extract_top_level_from_import(module: str) -> str:
    return module.split(".", 1)[0]


def _check_utils_py_allowlist(
    *,
    py_files: list[Path],
    root: Path,
    allowlist: set[str],
) -> list[ImportViolation]:
    violations: list[ImportViolation] = []

    for file_path in py_files:
        if file_path.name != "utils.py":
            continue
        rel = _norm_rel(file_path, root=root)
        if rel not in allowlist:
            violations.append(
                ImportViolation(
                    file=rel,
                    lineno=1,
                    message=(
                        "Заборонено додавати нові utils.py. "
                        "Додай виняток у tools/import_rules.toml лише якщо це справді необхідно."
                    ),
                )
            )

    return violations


def _matches_rule(importer_top_level: str, rule: dict[str, Any]) -> bool:
    rule_importer = rule.get("importer_top_level")
    if isinstance(rule_importer, str) and rule_importer:
        return importer_top_level == rule_importer

    rule_glob = rule.get("importer_glob")
    if isinstance(rule_glob, str) and rule_glob:
        return fnmatch(importer_top_level, rule_glob)

    return False


def _check_import_boundaries(
    *,
    py_files: list[Path],
    root: Path,
    rules: list[dict[str, Any]],
) -> list[ImportViolation]:
    violations: list[ImportViolation] = []

    for file_path in py_files:
        importer_top = _top_level_package(file_path, root=root)
        if not importer_top:
            continue

        applicable = [r for r in rules if _matches_rule(importer_top, r)]
        if not applicable:
            continue

        rel = _norm_rel(file_path, root=root)

        # Дозволяємо точкові винятки для окремих файлів (наприклад compat-шари).
        effective_rules: list[dict[str, Any]] = []
        for rule in applicable:
            allow_files = rule.get("allow_files")
            if isinstance(allow_files, list) and rel in {str(x) for x in allow_files}:
                continue
            effective_rules.append(rule)

        if not effective_rules:
            continue
        try:
            tree = ast.parse(file_path.read_text(encoding="utf-8"), filename=rel)
        except SyntaxError as exc:
            violations.append(
                ImportViolation(
                    file=rel,
                    lineno=int(getattr(exc, "lineno", 1) or 1),
                    message=f"Не вдалося розпарсити файл (SyntaxError): {exc}",
                )
            )
            continue
        except UnicodeDecodeError as exc:
            violations.append(
                ImportViolation(
                    file=rel,
                    lineno=1,
                    message=f"Не вдалося прочитати файл (encoding): {exc}",
                )
            )
            continue

        for node in ast.walk(tree):
            imported_top_levels: list[tuple[str, int]] = []

            if isinstance(node, ast.Import):
                for alias in node.names:
                    if not alias.name:
                        continue
                    imported_top_levels.append(
                        (_extract_top_level_from_import(alias.name), node.lineno or 1)
                    )

            elif isinstance(node, ast.ImportFrom):
                # Відносні імпорти (level>0) не розглядаємо як міжпакетні.
                if (node.level or 0) > 0:
                    continue
                if not node.module:
                    continue
                imported_top_levels.append(
                    (_extract_top_level_from_import(node.module), node.lineno or 1)
                )

            if not imported_top_levels:
                continue

            for rule in effective_rules:
                forbid = rule.get("forbid_top_level")
                if not isinstance(forbid, list):
                    continue

                forbid_set = {str(x) for x in forbid if str(x)}
                reason = rule.get("reason")
                reason_text = str(reason) if reason else "порушення меж модулів"

                for imported_top, lineno in imported_top_levels:
                    if imported_top in forbid_set:
                        violations.append(
                            ImportViolation(
                                file=rel,
                                lineno=lineno,
                                message=(
                                    f"Заборонений імпорт '{imported_top}' з пакету '{importer_top}'. "
                                    f"Причина: {reason_text}."
                                ),
                            )
                        )

    return violations


def _check_forbidden_strings(
    *,
    py_files: list[Path],
    root: Path,
    forbidden_strings: list[str],
) -> list[ImportViolation]:
    violations: list[ImportViolation] = []
    needles = [s for s in (str(x) for x in forbidden_strings) if s]
    if not needles:
        return violations

    for file_path in py_files:
        rel = _norm_rel(file_path, root=root)
        try:
            text = file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            violations.append(
                ImportViolation(
                    file=rel,
                    lineno=1,
                    message=f"Не вдалося прочитати файл (encoding): {exc}",
                )
            )
            continue

        for needle in needles:
            if needle not in text:
                continue
            violations.append(
                ImportViolation(
                    file=rel,
                    lineno=_first_line_number(text, needle),
                    message=(
                        f"Заборонений рядок '{needle}'. "
                        "Рекомендація: використовуй 'fxcm:price_tik'."
                    ),
                )
            )

    return violations


def _check_forbidden_import_prefixes(
    *,
    py_files: list[Path],
    root: Path,
    forbidden_prefixes: list[str],
) -> list[ImportViolation]:
    violations: list[ImportViolation] = []
    prefixes = [str(x).strip() for x in forbidden_prefixes if str(x).strip()]
    if not prefixes:
        return violations

    def _matches_prefix(module: str, prefix: str) -> bool:
        if module == prefix:
            return True
        return module.startswith(prefix + ".")

    for file_path in py_files:
        rel = _norm_rel(file_path, root=root)
        try:
            tree = ast.parse(file_path.read_text(encoding="utf-8"), filename=rel)
        except Exception:
            # Синтаксис/encoding вже перевіряються іншими рейками.
            continue

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    mod = alias.name or ""
                    for prefix in prefixes:
                        if _matches_prefix(mod, prefix):
                            violations.append(
                                ImportViolation(
                                    file=rel,
                                    lineno=node.lineno or 1,
                                    message=(
                                        f"Заборонений імпорт '{mod}'. "
                                        f"Префікс '{prefix}' заборонено рейкою D3."
                                    ),
                                )
                            )
                            break

            elif isinstance(node, ast.ImportFrom):
                if (node.level or 0) > 0:
                    continue
                mod = node.module or ""
                for prefix in prefixes:
                    if _matches_prefix(mod, prefix):
                        violations.append(
                            ImportViolation(
                                file=rel,
                                lineno=node.lineno or 1,
                                message=(
                                    f"Заборонений імпорт 'from {mod} import ...'. "
                                    f"Префікс '{prefix}' заборонено рейкою D3."
                                ),
                            )
                        )
                        break

    return violations


def main() -> int:
    root = _repo_root()
    config_path = root / "tools" / "import_rules.toml"
    if not config_path.exists():
        print("[B1] Не знайдено tools/import_rules.toml", file=sys.stderr)
        return 2

    try:
        cfg = _load_rules(config_path)
    except Exception as exc:
        print(f"[B1] Не вдалося прочитати конфіг: {exc}", file=sys.stderr)
        return 2

    scan_cfg_raw = cfg.get("scan")
    scan_cfg: dict[str, Any] = scan_cfg_raw if isinstance(scan_cfg_raw, dict) else {}
    ignore_dirs = set(str(x) for x in (scan_cfg.get("ignore_dirs") or []) if str(x))

    py_files = _iter_python_files(root, ignore_dirs=ignore_dirs)

    utils_cfg_raw = cfg.get("utils_files")
    utils_cfg: dict[str, Any] = utils_cfg_raw if isinstance(utils_cfg_raw, dict) else {}
    allowlist = set(str(x) for x in (utils_cfg.get("allowlist") or []) if str(x))

    violations: list[ImportViolation] = []
    violations.extend(
        _check_utils_py_allowlist(py_files=py_files, root=root, allowlist=allowlist)
    )

    rails_raw = cfg.get("rails")
    rails: dict[str, Any] = rails_raw if isinstance(rails_raw, dict) else {}

    forbidden_raw = rails.get("forbidden_strings")
    forbidden_strings: list[str] = (
        [str(x) for x in forbidden_raw] if isinstance(forbidden_raw, list) else []
    )
    violations.extend(
        _check_forbidden_strings(
            py_files=py_files,
            root=root,
            forbidden_strings=forbidden_strings,
        )
    )

    forbidden_import_prefixes_raw = rails.get("forbidden_import_prefixes")
    forbidden_import_prefixes: list[str] = (
        [str(x) for x in forbidden_import_prefixes_raw]
        if isinstance(forbidden_import_prefixes_raw, list)
        else []
    )
    violations.extend(
        _check_forbidden_import_prefixes(
            py_files=py_files,
            root=root,
            forbidden_prefixes=forbidden_import_prefixes,
        )
    )

    rules_raw = cfg.get("import_rules")
    rules: list[dict[str, Any]] = rules_raw if isinstance(rules_raw, list) else []
    violations.extend(
        _check_import_boundaries(py_files=py_files, root=root, rules=rules)
    )

    if not violations:
        print("[B1] OK: порушень меж модулів не знайдено.")
        return 0

    print("[B1] ПОМИЛКА: знайдено порушення меж модулів/рейок.")
    for v in sorted(violations, key=lambda x: (x.file, x.lineno, x.message)):
        print(f"- {v.file}:{v.lineno}: {v.message}")

    print(
        "\nПорада: якщо це легітимний виняток — додай його у tools/import_rules.toml з поясненням.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
