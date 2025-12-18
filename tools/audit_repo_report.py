"""Audit-репорт для контрольних точок репо (rails/SSOT/contracts).

Ціль: практично порахувати "скільки ще патчів" без гадання.
Скрипт друкує зведення та точні місця, де ще лишились:
- прямі `json.dumps/json.loads` і пов'язані анти-патерни (`default=str`, ручне ISO/UTC);
- локальні `TypedDict`/`SCHEMA_VERSION` поза `core/contracts/*`;
- порушення меж імпортів для `core/` (за `tools/import_rules.toml`).

Запуск (Windows/PowerShell):
- `python tools/audit_repo_report.py`

Режими:
- `--only-core`: сканує тільки `core/` (швидкий чек, чи core "чистий");
- `--top N`: показує top-N файлів за кількістю збігів у секції (за замовчуванням 10).
- `--include-tests`: включає `tests/**` у скан (за замовчуванням tests виключено);
- `--include-tools`: включає `tools/**` у скан (за замовчуванням tools виключено).

Приклади запуску:
- Повний audit (усе репо):
    - `python tools/audit_repo_report.py --include-tests --include-tools`
- Повний audit + top offenders (наприклад top-15):
    - `python tools/audit_repo_report.py --top 15 --include-tests --include-tools`
- Швидкий чек тільки для core/:
    - `python tools/audit_repo_report.py --only-core`
- core/ + top offenders (top-10):
    - `python tools/audit_repo_report.py --only-core --top 10`

Дефолт: production surface (без `tests/**` і `tools/**`).

Принцип: без зовнішніх залежностей; конфіг читаємо з `tools/import_rules.toml`.
"""

from __future__ import annotations

import argparse
import ast
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore[import-not-found]


@dataclass(frozen=True, slots=True)
class Finding:
    kind: str
    file: str
    line: int
    message: str


@dataclass(frozen=True, slots=True)
class TopOffender:
    file: str
    count: int


_AUDIT_LOCAL_SCHEMA_MARKER = "audit: local-schema"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _norm_rel(path: Path, *, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _load_import_rules(config_path: Path) -> dict[str, Any]:
    data = tomllib.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Некоректний формат tools/import_rules.toml")
    return data


def _iter_python_files(root: Path, *, ignore_dirs: set[str]) -> list[Path]:
    files: list[Path] = []

    def _is_ignored(path: Path) -> bool:
        return any(part in ignore_dirs for part in path.parts)

    for path in root.rglob("*.py"):
        if _is_ignored(path.parent):
            continue
        files.append(path)

    return files


def _filter_only_core(py_files: Iterable[Path], *, root: Path) -> list[Path]:
    """Повертає лише файли з `core/` (для швидкого режиму audit)."""

    filtered: list[Path] = []
    for path in py_files:
        rel = _norm_rel(path, root=root)
        if rel.startswith("core/"):
            filtered.append(path)
    return filtered


def _is_local_schema_file(file_path: Path) -> bool:
    """Повертає True, якщо файл явно помічений як local-schema для аудиту.

    Навіщо:
    - деякі TypedDict можуть бути внутрішніми для UI (не міжмодульний контракт);
    - щоб audit показував "реальні" C-борги, а не UI-local структури.

    Оптика: це не allowlist path — це явна декларація у файлі.
    """

    try:
        head = file_path.read_text(encoding="utf-8").splitlines()[:40]
    except Exception:
        return False
    return any(_AUDIT_LOCAL_SCHEMA_MARKER in line for line in head)


def _find_text_patterns(
    *,
    py_files: Iterable[Path],
    root: Path,
    allow_file_rel: set[str],
) -> list[Finding]:
    """Шукає текстові патерни JSON/часу поза allowlist файлів."""

    patterns: list[tuple[str, re.Pattern[str], str]] = [
        (
            "forbidden_helper:_as_dict",
            re.compile(r"^\s*def\s+_as_dict\s*\(", re.IGNORECASE),
            "Заборонено локальний def _as_dict(...) (використовуй core.serialization.coerce_dict)",
        ),
        (
            "forbidden_helper:_safe_float",
            re.compile(r"^\s*def\s+_safe_float\s*\(", re.IGNORECASE),
            "Заборонено локальний def _safe_float(...) (використовуй core.serialization.safe_float)",
        ),
        (
            "forbidden_helper:_safe_int",
            re.compile(r"^\s*def\s+_safe_int\s*\(", re.IGNORECASE),
            "Заборонено локальний def _safe_int(...) (використовуй core.serialization.safe_int)",
        ),
        (
            "json.dumps",
            re.compile(r"\bjson\.dumps\s*\(", re.IGNORECASE),
            "Прямий виклик json.dumps (SSOT має бути через core.serialization.json_dumps на I/O межі)",
        ),
        (
            "json.loads",
            re.compile(r"\bjson\.loads\s*\(", re.IGNORECASE),
            "Прямий виклик json.loads (SSOT має бути через core.serialization.json_loads на I/O межі)",
        ),
        (
            "default=str",
            re.compile(r"default\s*=\s*str\b"),
            "Анти-патерн default=str (використовуй core.serialization.to_jsonable/json_dumps)",
        ),
        (
            "isoformat()+Z",
            re.compile(r"isoformat\s*\(\s*\)\s*\+\s*['\"]Z['\"]"),
            'Ручне формування RFC3339 через isoformat()+"Z" (використовуй core.serialization.dt_to_iso_z)',
        ),
        (
            "replace(+00:00,Z)",
            re.compile(r"replace\(\s*['\"]\+00:00['\"]\s*,\s*['\"]Z['\"]\s*\)"),
            "Ручний .replace('+00:00','Z') (використовуй core.serialization.dt_to_iso_z)",
        ),
        (
            "datetime.utcnow",
            re.compile(r"\bdatetime\.utcnow\s*\("),
            "Ручний datetime.utcnow() (краще core.serialization.utc_now_ms або явний UTC datetime)",
        ),
        (
            "datetime.fromtimestamp(tz=UTC)",
            re.compile(
                r"\bdatetime\.fromtimestamp\s*\([^\)]*\btz\s*=\s*UTC", re.IGNORECASE
            ),
            "Ручний datetime.fromtimestamp(..., tz=UTC) (використовуй SSOT core.serialization.* хелпери для часу)",
        ),
        (
            "astimezone(UTC)",
            re.compile(r"\.astimezone\s*\(\s*UTC\s*\)"),
            "Ручне .astimezone(UTC) (перевір: чи це справді потрібно поза core.serialization)",
        ),
        (
            "replace(tzinfo=UTC)",
            re.compile(r"\.replace\s*\(\s*tzinfo\s*=\s*UTC\s*\)"),
            "Ручне .replace(tzinfo=UTC) (перевір: чи це справді потрібно поза core.serialization)",
        ),
    ]

    findings: list[Finding] = []

    for file_path in py_files:
        rel = _norm_rel(file_path, root=root)
        if rel in allow_file_rel:
            continue
        try:
            lines = file_path.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError as exc:
            findings.append(
                Finding(
                    kind="read_error",
                    file=rel,
                    line=1,
                    message=f"Не вдалося прочитати файл (encoding): {exc}",
                )
            )
            continue

        for line_idx, line in enumerate(lines, start=1):
            for kind, pattern, hint in patterns:
                if pattern.search(line):
                    findings.append(
                        Finding(
                            kind=kind,
                            file=rel,
                            line=line_idx,
                            message=hint,
                        )
                    )

    return findings


def _inherits_from_typed_dict(node: ast.ClassDef) -> bool:
    for base in node.bases:
        # class X(TypedDict):
        if isinstance(base, ast.Name) and base.id == "TypedDict":
            return True
        # class X(typing.TypedDict):
        if isinstance(base, ast.Attribute) and base.attr == "TypedDict":
            return True
    return False


def _find_contract_smells(
    *,
    py_files: Iterable[Path],
    root: Path,
) -> list[Finding]:
    """Шукає локальні TypedDict/SCHEMA_VERSION поза core/contracts/* (C1/C2 rails)."""

    findings: list[Finding] = []

    for file_path in py_files:
        rel = _norm_rel(file_path, root=root)

        # Канонічна зона для контрактів.
        if rel.startswith("core/contracts/"):
            continue

        if _is_local_schema_file(file_path):
            continue

        try:
            text = file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue

        try:
            tree = ast.parse(text, filename=rel)
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and _inherits_from_typed_dict(node):
                findings.append(
                    Finding(
                        kind="typed_dict_outside_contracts",
                        file=rel,
                        line=node.lineno or 1,
                        message=(
                            "TypedDict оголошено поза core/contracts/* (перенеси контракт або додай compat-правило/міграцію)"
                        ),
                    )
                )

            if isinstance(node, ast.Assign):
                # NAME = "..."  (наприклад UI_SMC_PAYLOAD_SCHEMA_VERSION)
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id.endswith(
                        "SCHEMA_VERSION"
                    ):
                        findings.append(
                            Finding(
                                kind="schema_version_constant_outside_contracts",
                                file=rel,
                                line=node.lineno or 1,
                                message=(
                                    "Константа *SCHEMA_VERSION поза core/contracts/* (перевір: чи це не дубль контракту)"
                                ),
                            )
                        )

    # Додатковий легкий текстовий сигнал: schema_version аннотації поза контрактами.
    # Важливо: у цьому ж файлі audit є рядок з повідомленням "schema_version: str",
    # тому виключаємо його, щоб не створювати фальшивий шум.
    schema_ann = re.compile(r"\bschema_version\s*:\s*str\b")
    for file_path in py_files:
        rel = _norm_rel(file_path, root=root)
        if rel == "tools/audit_repo_report.py":
            continue
        if rel.startswith("core/contracts/"):
            continue
        if _is_local_schema_file(file_path):
            continue
        try:
            lines = file_path.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            continue
        for idx, line in enumerate(lines, start=1):
            if schema_ann.search(line):
                findings.append(
                    Finding(
                        kind="schema_version_annotation_outside_contracts",
                        file=rel,
                        line=idx,
                        message="Поле schema_version: str поза core/contracts/* (перевір: чи це не локальна схема)",
                    )
                )

    return findings


def _find_utils_smells(
    *,
    py_files: Iterable[Path],
    root: Path,
    utils_allowlist: set[str],
) -> list[Finding]:
    findings: list[Finding] = []

    for file_path in py_files:
        rel = _norm_rel(file_path, root=root)

        if file_path.name == "utils.py" and rel not in utils_allowlist:
            findings.append(
                Finding(
                    kind="new_utils_py",
                    file=rel,
                    line=1,
                    message="utils.py поза allowlist (rails B1): не додаємо нові utils.py",
                )
            )

        rel_lower = rel.lower()
        if "utils" in rel_lower and rel not in utils_allowlist:
            # Не піднімаємо шум на відомий top-level utils/ (він уже в allowlist як utils/utils.py).
            # Але все одно показуємо "utils"-сліди у шляхах як підказку для інвентаризації.
            findings.append(
                Finding(
                    kind="utils_in_path",
                    file=rel,
                    line=1,
                    message="Шлях містить 'utils' (перевір: чи це не новий dump/утиліта, яку варто нормалізувати)",
                )
            )

    # Дедуп для utils_in_path, бо вище може додати шум.
    seen: set[tuple[str, str]] = set()
    deduped: list[Finding] = []
    for f in findings:
        key = (f.kind, f.file)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(f)

    return deduped


def _extract_core_no_upstream_rule(
    import_rules: dict[str, Any],
) -> tuple[set[str], set[str]]:
    """Повертає (forbid_top_level, allow_files) для core_no_upstream."""

    rules = import_rules.get("import_rules")
    if not isinstance(rules, list):
        return set(), set()

    for rule in rules:
        if not isinstance(rule, dict):
            continue
        if rule.get("name") != "core_no_upstream":
            continue
        forbid = rule.get("forbid_top_level")
        allow_files = rule.get("allow_files")
        forbid_set = {str(x) for x in forbid} if isinstance(forbid, list) else set()
        allow_set = (
            {str(x) for x in allow_files} if isinstance(allow_files, list) else set()
        )
        return forbid_set, allow_set

    return set(), set()


def _top_level_from_module(module: str) -> str:
    return module.split(".", 1)[0]


def _find_core_import_violations(
    *,
    py_files: Iterable[Path],
    root: Path,
    forbid_top: set[str],
    allow_files: set[str],
) -> list[Finding]:
    findings: list[Finding] = []

    for file_path in py_files:
        rel = _norm_rel(file_path, root=root)
        if not rel.startswith("core/"):
            continue
        if rel in allow_files:
            continue

        try:
            text = file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue

        try:
            tree = ast.parse(text, filename=rel)
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if not alias.name:
                        continue
                    imported_top = _top_level_from_module(alias.name)
                    if imported_top in forbid_top:
                        findings.append(
                            Finding(
                                kind="core_import_boundary_violation",
                                file=rel,
                                line=node.lineno or 1,
                                message=f"core/ імпортує '{imported_top}' (заборонено правилами меж)",
                            )
                        )

            if isinstance(node, ast.ImportFrom):
                if (node.level or 0) > 0:
                    continue
                if not node.module:
                    continue
                imported_top = _top_level_from_module(node.module)
                if imported_top in forbid_top:
                    findings.append(
                        Finding(
                            kind="core_import_boundary_violation",
                            file=rel,
                            line=node.lineno or 1,
                            message=f"core/ імпортує '{imported_top}' (заборонено правилами меж)",
                        )
                    )

    return findings


def _print_section(title: str) -> None:
    print("\n" + "=" * 80)
    print(title)
    print("=" * 80)


def _top_offenders(findings: list[Finding], *, top_n: int) -> list[TopOffender]:
    if top_n <= 0:
        return []
    counts: dict[str, int] = {}
    for f in findings:
        counts[f.file] = counts.get(f.file, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return [TopOffender(file=file, count=count) for file, count in ranked[:top_n]]


def _print_findings(
    findings: list[Finding],
    *,
    limit: int = 60,
    top_n: int = 10,
) -> None:
    if not findings:
        print("OK: не знайдено")
        return

    print(f"Знайдено: {len(findings)}")

    offenders = _top_offenders(findings, top_n=top_n)
    if offenders:
        print("Top offenders (файл → кількість):")
        for o in offenders:
            print(f"- {o.file} → {o.count}")
        print("-")

    shown = 0
    for f in sorted(findings, key=lambda x: (x.file, x.line, x.kind)):
        print(f"- {f.file}:{f.line} [{f.kind}] {f.message}")
        shown += 1
        if shown >= limit:
            remain = len(findings) - shown
            if remain > 0:
                print(f"… ще {remain} (запусти локально, щоб побачити весь список)")
            break


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit-репорт: рейки/SSOT/contracts (підрахунок залишків патчів)",
    )
    parser.add_argument(
        "--only-core",
        action="store_true",
        help="Сканувати тільки core/ (швидкий режим)",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=10,
        help="Скільки top offenders показувати у кожній секції (за замовчуванням 10)",
    )
    parser.add_argument(
        "--include-tests",
        action="store_true",
        help="Включити tests/** у скан (за замовчуванням tests виключено)",
    )
    parser.add_argument(
        "--include-tools",
        action="store_true",
        help="Включити tools/** у скан (за замовчуванням tools виключено)",
    )
    args = parser.parse_args()

    root = _repo_root()
    rules_path = root / "tools" / "import_rules.toml"

    rules = _load_import_rules(rules_path)
    scan_value = rules.get("scan")
    scan_cfg: dict[str, Any] = scan_value if isinstance(scan_value, dict) else {}
    ignore_dirs_raw = scan_cfg.get("ignore_dirs", [])
    ignore_dirs = set(ignore_dirs_raw) if isinstance(ignore_dirs_raw, list) else set()

    # B4: дефолтно дивимось "production surface" без tests/ та tools/.
    # За потреби повного інвентаря вмикаємо флагами.
    if not args.include_tests:
        ignore_dirs.add("tests")
    if not args.include_tools:
        ignore_dirs.add("tools")

    utils_value = rules.get("utils_files")
    utils_cfg: dict[str, Any] = utils_value if isinstance(utils_value, dict) else {}
    allowlist_raw = utils_cfg.get("allowlist", [])
    utils_allowlist = (
        {str(x) for x in allowlist_raw} if isinstance(allowlist_raw, list) else set()
    )

    py_files_all = _iter_python_files(root, ignore_dirs=ignore_dirs)
    py_files = (
        _filter_only_core(py_files_all, root=root) if args.only_core else py_files_all
    )

    # Allowlist файлів для SSOT-патернів (де вони легальні за задумом).
    allow_file_rel = {
        "core/serialization.py",
        # tools/ і tests/ можуть мати json.dumps/json.loads у debug-режимі,
        # але для аудиту ми їх показуємо (не allowlist), щоб порахувати залишок.
    }

    forbid_top, allow_files = _extract_core_no_upstream_rule(rules)

    _print_section("A) Межі/рейки (core import boundaries)")
    core_violations = _find_core_import_violations(
        py_files=py_files,
        root=root,
        forbid_top=forbid_top,
        allow_files=allow_files,
    )
    _print_findings(core_violations, top_n=args.top)

    _print_section("B) SSOT серіалізація/час (поза core/serialization.py)")
    json_time_findings = _find_text_patterns(
        py_files=py_files,
        root=root,
        allow_file_rel=allow_file_rel,
    )
    _print_findings(json_time_findings, top_n=args.top)

    _print_section("C) Контракти / schema_version (локальні TypedDict/SCHEMA_VERSION)")
    contract_smells = _find_contract_smells(py_files=py_files, root=root)
    _print_findings(contract_smells, top_n=args.top)

    _print_section("D) Потенційні utils (імена/шляхи)")
    utils_smells = _find_utils_smells(
        py_files=py_files,
        root=root,
        utils_allowlist=utils_allowlist,
    )
    _print_findings(utils_smells, top_n=args.top)

    # Exit code: блокуємо лише критичні порушення (production surface за замовчуванням).
    # A) Межі core/
    # B) SSOT серіалізація/час і заборонені локальні хелпери
    # D) Нові utils.py
    hard_b_kinds = {
        "forbidden_helper:_as_dict",
        "forbidden_helper:_safe_float",
        "forbidden_helper:_safe_int",
        "json.dumps",
        "json.loads",
        "default=str",
        "isoformat()+Z",
        "replace(+00:00,Z)",
        "datetime.utcnow",
        "datetime.fromtimestamp(tz=UTC)",
        "read_error",
    }

    hard = [f for f in core_violations if f.kind == "core_import_boundary_violation"]
    hard += [f for f in json_time_findings if f.kind in hard_b_kinds]
    hard += [f for f in utils_smells if f.kind == "new_utils_py"]

    if hard:
        print(
            "\nFAIL: є блокуючі порушення рейок (A, критичні B-SSOT, або нові utils.py)"
        )
        return 2

    print("\nOK: жорсткі рейки пройдено; решта — інвентаризація")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
