from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


METADATA_KINDS = {
    "Catalogs": "catalog",
    "Documents": "document",
    "InformationRegisters": "information_register",
    "AccumulationRegisters": "accumulation_register",
    "AccountingRegisters": "accounting_register",
    "CalculationRegisters": "calculation_register",
    "BusinessProcesses": "business_process",
    "Tasks": "task",
    "Enums": "enum",
    "Enumerations": "enum",
    "CommonModules": "common_module",
    "Reports": "report",
    "DataProcessors": "data_processor",
    "ChartsOfCharacteristicTypes": "chart_of_characteristic_types",
    "ChartsOfAccounts": "chart_of_accounts",
    "ChartsOfCalculationTypes": "chart_of_calculation_types",
    "ExchangePlans": "exchange_plan",
    "Constants": "constant",
    "DefinedTypes": "defined_type",
    "HTTPServices": "http_service",
    "WebServices": "web_service",
}

SYMBOL_RE = re.compile(
    r"(?im)^\s*(?:Процедура|Функция|Procedure|Function)\s+"
    r"(?P<name>[A-Za-zА-Яа-яЁё_][A-Za-zА-Яа-яЁё0-9_]*)\s*\("
)


@dataclass(slots=True, frozen=True)
class MetadataObject:
    kind: str
    name: str
    definition_path: str
    modules: tuple[str, ...] = ()


@dataclass(slots=True, frozen=True)
class BslSymbol:
    name: str
    path: str
    line: int


@dataclass(slots=True)
class ConfigurationIndex:
    root: Path
    objects: list[MetadataObject] = field(default_factory=list)
    symbols: list[BslSymbol] = field(default_factory=list)

    @classmethod
    def build(cls, root: Path) -> ConfigurationIndex:
        root = root.expanduser().resolve()
        index = cls(root=root)
        modules_by_object: dict[tuple[str, str], list[str]] = {}

        for path in root.rglob("*.bsl"):
            if not path.is_file() or ".onec-harness" in path.parts:
                continue
            relative = path.relative_to(root)
            parts = relative.parts
            if len(parts) >= 2 and parts[0] in METADATA_KINDS:
                modules_by_object.setdefault((parts[0], parts[1]), []).append(relative.as_posix())
            try:
                text = path.read_text(encoding="utf-8-sig")
            except (UnicodeDecodeError, OSError):
                continue
            for match in SYMBOL_RE.finditer(text):
                line = text.count("\n", 0, match.start()) + 1
                index.symbols.append(BslSymbol(name=match.group("name"), path=relative.as_posix(), line=line))

        for folder, kind in METADATA_KINDS.items():
            folder_path = root / folder
            if not folder_path.exists():
                continue
            for definition in sorted(folder_path.glob("*.xml")):
                name = definition.stem
                modules = tuple(sorted(modules_by_object.get((folder, name), [])))
                index.objects.append(
                    MetadataObject(kind=kind, name=name, definition_path=definition.relative_to(root).as_posix(), modules=modules)
                )
                forms_dir = folder_path / name / "Forms"
                if not forms_dir.exists():
                    continue
                for form_definition in sorted(forms_dir.glob("*.xml")):
                    form_name = form_definition.stem
                    form_module = forms_dir / form_name / "Ext" / "Form" / "Module.bsl"
                    form_modules = (form_module.relative_to(root).as_posix(),) if form_module.exists() else ()
                    index.objects.append(
                        MetadataObject(
                            kind="form",
                            name=f"{kind}.{name}.{form_name}",
                            definition_path=form_definition.relative_to(root).as_posix(),
                            modules=form_modules,
                        )
                    )

        index.objects.sort(key=lambda item: (item.kind, item.name.casefold()))
        index.symbols.sort(key=lambda item: (item.name.casefold(), item.path, item.line))
        return index

    def find_objects(self, query: str = "", kind: str | None = None) -> list[MetadataObject]:
        normalized_query = query.casefold().strip()
        normalized_kind = kind.casefold().strip() if kind else None
        result: list[MetadataObject] = []
        for item in self.objects:
            if normalized_kind and item.kind.casefold() != normalized_kind:
                continue
            if normalized_query:
                haystack = f"{item.kind} {item.name} {item.definition_path}".casefold()
                if normalized_query not in haystack:
                    continue
            result.append(item)
        return result

    def find_symbols(self, query: str) -> list[BslSymbol]:
        normalized = query.casefold().strip()
        if not normalized:
            return []
        return [item for item in self.symbols if normalized in item.name.casefold()]

    def describe_objects(self, query: str = "", limit: int = 100) -> str:
        matches = self.find_objects(query=query)[:limit]
        if not matches:
            return "No metadata objects found"
        lines: list[str] = []
        for item in matches:
            modules = ", ".join(item.modules) if item.modules else "no BSL modules"
            lines.append(f"{item.kind}:{item.name} | {item.definition_path} | {modules}")
        return "\n".join(lines)

    def describe_symbols(self, query: str, limit: int = 100) -> str:
        matches = self.find_symbols(query)[:limit]
        if not matches:
            return "No BSL symbols found"
        return "\n".join(f"{item.name} | {item.path}:{item.line}" for item in matches)
