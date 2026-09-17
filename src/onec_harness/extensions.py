from __future__ import annotations

import re
import uuid
from pathlib import Path

from onec_harness.semantic import NAMESPACE_LINE, SemanticChange, SemanticMetadataError
from onec_harness.snapshots import SnapshotStore
from onec_harness.workspace import Workspace


UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


class ExtensionSourceError(SemanticMetadataError):
    pass


class ExtensionSourceManager:
    """Safe source-level operations for already dumped 1C configuration extensions."""

    _KINDS = {
        "catalog": ("Catalogs", "Catalog"),
        "document": ("Documents", "Document"),
        "information_register": ("InformationRegisters", "InformationRegister"),
        "accumulation_register": ("AccumulationRegisters", "AccumulationRegister"),
    }
    _MODULES = {
        "catalog": {"object": "ObjectModule.bsl", "manager": "ManagerModule.bsl"},
        "document": {"object": "ObjectModule.bsl", "manager": "ManagerModule.bsl"},
        "information_register": {"recordset": "RecordSetModule.bsl", "manager": "ManagerModule.bsl"},
        "accumulation_register": {"recordset": "RecordSetModule.bsl", "manager": "ManagerModule.bsl"},
    }
    _INTERCEPTORS = {
        "before": "Перед",
        "after": "После",
        "instead": "Вместо",
        "перед": "Перед",
        "после": "После",
        "вместо": "Вместо",
    }

    def __init__(self, workspace: Workspace) -> None:
        self.workspace = workspace
        self.snapshots = SnapshotStore(workspace)

    @staticmethod
    def _identifier(value: str, label: str) -> str:
        return re.sub(r"\s+", "", value) if False else _validate_identifier(value, label)

    @staticmethod
    def _kind(kind: str) -> tuple[str, str, str]:
        normalized = kind.strip().lower().replace("-", "_")
        value = ExtensionSourceManager._KINDS.get(normalized)
        if value is None:
            raise ExtensionSourceError(f"Unsupported extension metadata kind: {kind!r}")
        return normalized, value[0], value[1]

    def extension_root(self, extension: str) -> Path:
        name = self._identifier(extension, "extension name")
        root = Path("Extensions") / name
        config = self.workspace.resolve(root / "Configuration.xml")
        if not config.exists():
            raise ExtensionSourceError(
                f"Extension source was not found: {root}. Dump it first with onec-harness dump-extension {name}."
            )
        return root

    @staticmethod
    def _freshen_uuids(text: str) -> str:
        mapping: dict[str, str] = {}

        def repl(match: re.Match[str]) -> str:
            old = match.group(0).lower()
            mapping.setdefault(old, str(uuid.uuid4()))
            return mapping[old]

        return UUID_RE.sub(repl, text)

    @staticmethod
    def _extract_root(text: str, tag: str) -> tuple[str, str]:
        root = re.search(rf"<{tag}\b[^>]*\buuid=\"([^\"]+)\"[^>]*>", text)
        if root is None:
            raise ExtensionSourceError(f"Base metadata XML has no {tag} root UUID")
        internal = re.search(r"<InternalInfo>.*?</InternalInfo>", text, re.S)
        if internal is None:
            raise ExtensionSourceError("Base metadata XML has no InternalInfo block")
        return root.group(1), internal.group(0)

    @staticmethod
    def _register_child(config_text: str, tag: str, name: str) -> str:
        entry = f"<{tag}>{name}</{tag}>"
        if entry in config_text:
            return config_text
        self_closing = list(re.finditer(r"(?m)^(?P<i>[ \t]*)<ChildObjects\s*/>\s*$", config_text))
        if self_closing:
            match = self_closing[-1]
            indent = match.group("i")
            block = f"{indent}<ChildObjects>\n{indent}\t{entry}\n{indent}</ChildObjects>"
            return config_text[:match.start()] + block + config_text[match.end():]
        closings = list(re.finditer(r"(?m)^(?P<i>[ \t]*)</ChildObjects>\s*$", config_text))
        if not closings:
            raise ExtensionSourceError("Extension Configuration.xml has no ChildObjects")
        closing = closings[-1]
        indent = closing.group("i")
        return config_text[:closing.start()] + f"{indent}\t{entry}\n" + config_text[closing.start():]

    def borrow_object(self, extension: str, kind: str, object_name: str) -> SemanticChange:
        normalized_kind, folder, tag = self._kind(kind)
        object_name = self._identifier(object_name, "object name")
        extension_root = self.extension_root(extension)
        base_path = Path(folder) / f"{object_name}.xml"
        base_file = self.workspace.resolve(base_path)
        if not base_file.exists():
            raise ExtensionSourceError(f"Base metadata object was not found: {normalized_kind}.{object_name}")
        target_path = extension_root / folder / f"{object_name}.xml"
        if self.workspace.resolve(target_path).exists():
            raise ExtensionSourceError(f"Object is already present in extension: {normalized_kind}.{object_name}")

        base_text = base_file.read_text(encoding="utf-8-sig")
        base_uuid, internal = self._extract_root(base_text, tag)
        version_match = re.search(r'<MetaDataObject\b[^>]*\bversion="([^"]+)"', base_text)
        version = version_match.group(1) if version_match else "2.17"
        internal = self._freshen_uuids(internal)
        xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<MetaDataObject {NAMESPACE_LINE} version="{version}">
\t<{tag} uuid="{uuid.uuid4()}">
\t\t{internal}
\t\t<Properties>
\t\t\t<ObjectBelonging>Adopted</ObjectBelonging>
\t\t\t<Name>{object_name}</Name>
\t\t\t<Comment/>
\t\t\t<ExtendedConfigurationObject>{base_uuid}</ExtendedConfigurationObject>
\t\t</Properties>
\t\t<ChildObjects/>
\t</{tag}>
</MetaDataObject>
'''
        config_path = extension_root / "Configuration.xml"
        snapshot = self.snapshots.create([config_path, target_path])
        self.workspace.write_text(target_path, xml)
        config_file = self.workspace.resolve(config_path)
        config_text = config_file.read_text(encoding="utf-8-sig")
        config_file.write_text(self._register_child(config_text, tag, object_name), encoding="utf-8")
        return SemanticChange(
            summary=f"Borrowed {tag}.{object_name} into extension {extension}",
            snapshot_id=snapshot.snapshot_id,
            paths=snapshot.paths,
            requires_reload=True,
        )

    def patch_method(
        self,
        extension: str,
        kind: str,
        object_name: str,
        method_name: str,
        *,
        interceptor: str = "Before",
        module: str = "object",
        handler_name: str | None = None,
        parameters: list[str] | None = None,
        body: str = "// TODO: implement",
        context: str | None = None,
        function: bool = False,
    ) -> SemanticChange:
        normalized_kind, folder, _ = self._kind(kind)
        object_name = self._identifier(object_name, "object name")
        method_name = self._identifier(method_name, "method name")
        extension_root = self.extension_root(extension)
        borrowed = extension_root / folder / f"{object_name}.xml"
        if not self.workspace.resolve(borrowed).exists():
            raise ExtensionSourceError(f"Borrow {normalized_kind}.{object_name} before patching its method")
        module_key = module.strip().lower()
        module_name = self._MODULES.get(normalized_kind, {}).get(module_key)
        if module_name is None:
            allowed = ", ".join(self._MODULES.get(normalized_kind, {}))
            raise ExtensionSourceError(f"Unsupported module {module!r} for {normalized_kind}; allowed: {allowed}")
        annotation = self._INTERCEPTORS.get(interceptor.strip().lower())
        if annotation is None:
            raise ExtensionSourceError("interceptor must be Before, After or Instead")
        handler = self._identifier(handler_name or f"Harness_{method_name}_{annotation}", "handler name")
        params = parameters or []
        for param in params:
            self._identifier(param, "parameter name")
        module_path = extension_root / folder / object_name / "Ext" / module_name
        target = self.workspace.resolve(module_path)
        existing = target.read_text(encoding="utf-8-sig") if target.exists() else ""
        if re.search(rf'&{annotation}\("{re.escape(method_name)}"\)', existing):
            raise ExtensionSourceError(f"Interceptor already exists: {annotation}({method_name})")
        if re.search(rf"(?im)^\s*(?:Процедура|Функция)\s+{re.escape(handler)}\s*\(", existing):
            raise ExtensionSourceError(f"Handler already exists: {handler}")

        keyword, end_keyword = ("Функция", "КонецФункции") if function else ("Процедура", "КонецПроцедуры")
        context_line = f"&{context.strip()}\n" if context and context.strip() else ""
        body_lines = body.strip() or "// TODO: implement"
        body_lines = "\n".join(f"\t{line}" for line in body_lines.splitlines())
        block = (
            f'{context_line}&{annotation}("{method_name}")\n'
            f"{keyword} {handler}({', '.join(params)})\n"
            f"{body_lines}\n{end_keyword}\n"
        )
        snapshot = self.snapshots.create([module_path])
        separator = "" if not existing else ("\n" if existing.endswith("\n") else "\n\n")
        self.workspace.write_text(module_path, existing + separator + block)
        return SemanticChange(
            summary=f"Added {annotation} interceptor for {method_name} in extension {extension}",
            snapshot_id=snapshot.snapshot_id,
            paths=snapshot.paths,
            requires_reload=True,
        )


def _validate_identifier(value: str, label: str) -> str:
    stripped = value.strip()
    if not re.fullmatch(r"[A-Za-zА-Яа-яЁё_][A-Za-zА-Яа-яЁё0-9_]*", stripped):
        raise ExtensionSourceError(f"Invalid 1C {label}: {value!r}")
    return stripped
