from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import escape

from onec_harness.snapshots import SnapshotInfo, SnapshotStore
from onec_harness.workspace import Workspace


IDENTIFIER_RE = re.compile(r"^[A-Za-zА-Яа-яЁё_][A-Za-zА-Яа-яЁё0-9_]*$")
REFERENCE_TYPE_RE = re.compile(
    r"^(CatalogRef|DocumentRef|EnumRef|ChartOfAccountsRef|ChartOfCharacteristicTypesRef)\."
    r"([A-Za-zА-Яа-яЁё_][A-Za-zА-Яа-яЁё0-9_]*)$"
)

NAMESPACE_LINE = (
    'xmlns="http://v8.1c.ru/8.3/MDClasses" '
    'xmlns:app="http://v8.1c.ru/8.2/managed-application/core" '
    'xmlns:cfg="http://v8.1c.ru/8.1/data/enterprise/current-config" '
    'xmlns:cmi="http://v8.1c.ru/8.2/managed-application/cmi" '
    'xmlns:ent="http://v8.1c.ru/8.1/data/enterprise" '
    'xmlns:lf="http://v8.1c.ru/8.2/managed-application/logform" '
    'xmlns:style="http://v8.1c.ru/8.1/data/ui/style" '
    'xmlns:sys="http://v8.1c.ru/8.1/data/ui/fonts/system" '
    'xmlns:v8="http://v8.1c.ru/8.1/data/core" '
    'xmlns:v8ui="http://v8.1c.ru/8.1/data/ui" '
    'xmlns:web="http://v8.1c.ru/8.1/data/ui/colors/web" '
    'xmlns:win="http://v8.1c.ru/8.1/data/ui/colors/windows" '
    'xmlns:xen="http://v8.1c.ru/8.3/xcf/enums" '
    'xmlns:xpr="http://v8.1c.ru/8.3/xcf/predef" '
    'xmlns:xr="http://v8.1c.ru/8.3/xcf/readable" '
    'xmlns:xs="http://www.w3.org/2001/XMLSchema" '
    'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"'
)


class SemanticMetadataError(RuntimeError):
    pass


@dataclass(slots=True, frozen=True)
class SemanticChange:
    summary: str
    snapshot_id: str
    paths: tuple[str, ...]
    requires_reload: bool = True


class MetadataEditor:
    """High-level operations over a hierarchical 1C XML configuration dump.

    The editor intentionally supports a narrow set of deterministic operations.
    Every mutation is snapshotted before touching files. The resulting dump still
    has to pass Designer /LoadConfigFromFiles + /CheckModules + /CheckConfig before
    it can be considered valid for a particular platform/configuration version.
    """

    _KINDS = {
        "catalog": ("Catalogs", "Catalog"),
        "document": ("Documents", "Document"),
    }

    _MODULES = {
        "object": "ObjectModule.bsl",
        "manager": "ManagerModule.bsl",
    }

    def __init__(self, workspace: Workspace) -> None:
        self.workspace = workspace
        self.snapshots = SnapshotStore(workspace)

    @staticmethod
    def _identifier(value: str, label: str = "identifier") -> str:
        value = value.strip()
        if not IDENTIFIER_RE.fullmatch(value):
            raise SemanticMetadataError(f"Invalid 1C {label}: {value!r}")
        return value

    def _kind(self, kind: str) -> tuple[str, str]:
        normalized = kind.strip().lower().replace("-", "_")
        if normalized not in self._KINDS:
            raise SemanticMetadataError(f"Unsupported metadata kind: {kind!r}")
        return self._KINDS[normalized]

    def _object_path(self, kind: str, name: str) -> Path:
        folder, _ = self._kind(kind)
        return Path(folder) / f"{self._identifier(name, 'object name')}.xml"

    def _format_version(self) -> str:
        config_path = self.workspace.resolve("Configuration.xml")
        if not config_path.exists():
            raise SemanticMetadataError("Configuration.xml was not found in workspace")
        text = config_path.read_text(encoding="utf-8-sig")
        match = re.search(r'<MetaDataObject\b[^>]*\bversion="([^"]+)"', text)
        return match.group(1) if match else "2.17"

    @staticmethod
    def _new_ids(count: int) -> list[str]:
        return [str(uuid.uuid4()) for _ in range(count)]

    def _register_root_child(self, tag: str, name: str) -> None:
        config_path = self.workspace.resolve("Configuration.xml")
        text = config_path.read_text(encoding="utf-8-sig")
        entry = f"<{tag}>{name}</{tag}>"
        if entry in text:
            raise SemanticMetadataError(f"Configuration already contains {tag}.{name}")

        self_closing = re.search(r"(?m)^(?P<i>[ \t]*)<ChildObjects\s*/>\s*$", text)
        if self_closing:
            indent = self_closing.group("i")
            replacement = f"{indent}<ChildObjects>\n{indent}\t{entry}\n{indent}</ChildObjects>"
            text = text[: self_closing.start()] + replacement + text[self_closing.end() :]
        else:
            closings = list(re.finditer(r"(?m)^(?P<i>[ \t]*)</ChildObjects>\s*$", text))
            if not closings:
                raise SemanticMetadataError("Configuration.xml has no root ChildObjects section")
            closing = closings[-1]
            indent = closing.group("i")
            text = text[: closing.start()] + f"{indent}\t{entry}\n" + text[closing.start() :]

        config_path.write_text(text, encoding="utf-8")

    @staticmethod
    def _generated_types(prefix: str, name: str, ids: list[str]) -> str:
        categories = [
            ("Object", "Object"),
            ("Ref", "Ref"),
            ("Selection", "Selection"),
            ("List", "List"),
            ("Manager", "Manager"),
        ]
        lines = ["\t\t<InternalInfo>"]
        for index, (suffix, category) in enumerate(categories):
            type_id = ids[1 + index * 2]
            value_id = ids[2 + index * 2]
            lines.extend(
                [
                    f'\t\t\t<xr:GeneratedType name="{prefix}{suffix}.{name}" category="{category}">',
                    f"\t\t\t\t<xr:TypeId>{type_id}</xr:TypeId>",
                    f"\t\t\t\t<xr:ValueId>{value_id}</xr:ValueId>",
                    "\t\t\t</xr:GeneratedType>",
                ]
            )
        lines.append("\t\t</InternalInfo>")
        return "\n".join(lines)

    def _catalog_xml(self, name: str, synonym: str, *, hierarchical: bool) -> str:
        ids = self._new_ids(11)
        version = self._format_version()
        generated = self._generated_types("Catalog", name, ids)
        synonym_xml = escape(synonym)
        return f'''<?xml version="1.0" encoding="UTF-8"?>
<MetaDataObject {NAMESPACE_LINE} version="{version}">
\t<Catalog uuid="{ids[0]}">
{generated}
\t\t<Properties>
\t\t\t<Name>{name}</Name>
\t\t\t<Synonym><v8:item><v8:lang>ru</v8:lang><v8:content>{synonym_xml}</v8:content></v8:item></Synonym>
\t\t\t<Comment/>
\t\t\t<Hierarchical>{str(hierarchical).lower()}</Hierarchical>
\t\t\t<HierarchyType>HierarchyFoldersAndItems</HierarchyType>
\t\t\t<LimitLevelCount>false</LimitLevelCount>
\t\t\t<LevelCount>2</LevelCount>
\t\t\t<FoldersOnTop>true</FoldersOnTop>
\t\t\t<UseStandardCommands>true</UseStandardCommands>
\t\t\t<Owners/>
\t\t\t<SubordinationUse>ToItems</SubordinationUse>
\t\t\t<CodeLength>9</CodeLength>
\t\t\t<DescriptionLength>100</DescriptionLength>
\t\t\t<CodeType>String</CodeType>
\t\t\t<CodeAllowedLength>Variable</CodeAllowedLength>
\t\t\t<CodeSeries>WholeCatalog</CodeSeries>
\t\t\t<CheckUnique>false</CheckUnique>
\t\t\t<Autonumbering>true</Autonumbering>
\t\t\t<DefaultPresentation>AsDescription</DefaultPresentation>
\t\t\t<Characteristics/>
\t\t\t<PredefinedDataUpdate>Auto</PredefinedDataUpdate>
\t\t\t<EditType>InDialog</EditType>
\t\t\t<QuickChoice>false</QuickChoice>
\t\t\t<ChoiceMode>BothWays</ChoiceMode>
\t\t\t<InputByString>
\t\t\t\t<xr:Field>Catalog.{name}.StandardAttribute.Description</xr:Field>
\t\t\t\t<xr:Field>Catalog.{name}.StandardAttribute.Code</xr:Field>
\t\t\t</InputByString>
\t\t\t<SearchStringModeOnInputByString>Begin</SearchStringModeOnInputByString>
\t\t\t<FullTextSearchOnInputByString>DontUse</FullTextSearchOnInputByString>
\t\t\t<ChoiceDataGetModeOnInputByString>Directly</ChoiceDataGetModeOnInputByString>
\t\t\t<DefaultObjectForm/><DefaultFolderForm/><DefaultListForm/><DefaultChoiceForm/><DefaultFolderChoiceForm/>
\t\t\t<AuxiliaryObjectForm/><AuxiliaryFolderForm/><AuxiliaryListForm/><AuxiliaryChoiceForm/><AuxiliaryFolderChoiceForm/>
\t\t\t<IncludeHelpInContents>false</IncludeHelpInContents>
\t\t\t<BasedOn/><DataLockFields/><DataLockControlMode>Managed</DataLockControlMode>
\t\t\t<FullTextSearch>Use</FullTextSearch>
\t\t\t<ObjectPresentation/><ExtendedObjectPresentation/><ListPresentation/><ExtendedListPresentation/><Explanation/>
\t\t\t<CreateOnInput>Use</CreateOnInput><ChoiceHistoryOnInput>Auto</ChoiceHistoryOnInput>
\t\t\t<DataHistory>DontUse</DataHistory><UpdateDataHistoryImmediatelyAfterWrite>false</UpdateDataHistoryImmediatelyAfterWrite>
\t\t\t<ExecuteAfterWriteDataHistoryVersionProcessing>false</ExecuteAfterWriteDataHistoryVersionProcessing>
\t\t</Properties>
\t\t<ChildObjects/>
\t</Catalog>
</MetaDataObject>
'''

    def _document_xml(self, name: str, synonym: str, *, posting: bool) -> str:
        ids = self._new_ids(11)
        version = self._format_version()
        generated = self._generated_types("Document", name, ids)
        synonym_xml = escape(synonym)
        posting_value = "Allow" if posting else "Deny"
        return f'''<?xml version="1.0" encoding="UTF-8"?>
<MetaDataObject {NAMESPACE_LINE} version="{version}">
\t<Document uuid="{ids[0]}">
{generated}
\t\t<Properties>
\t\t\t<Name>{name}</Name>
\t\t\t<Synonym><v8:item><v8:lang>ru</v8:lang><v8:content>{synonym_xml}</v8:content></v8:item></Synonym>
\t\t\t<Comment/><UseStandardCommands>true</UseStandardCommands><Numerator/>
\t\t\t<NumberType>String</NumberType><NumberLength>11</NumberLength><NumberAllowedLength>Variable</NumberAllowedLength>
\t\t\t<NumberPeriodicity>Year</NumberPeriodicity><CheckUnique>true</CheckUnique><Autonumbering>true</Autonumbering>
\t\t\t<Characteristics/><BasedOn/>
\t\t\t<InputByString><xr:Field>Document.{name}.StandardAttribute.Number</xr:Field></InputByString>
\t\t\t<CreateOnInput>Use</CreateOnInput><SearchStringModeOnInputByString>Begin</SearchStringModeOnInputByString>
\t\t\t<FullTextSearchOnInputByString>DontUse</FullTextSearchOnInputByString>
\t\t\t<ChoiceDataGetModeOnInputByString>Directly</ChoiceDataGetModeOnInputByString>
\t\t\t<DefaultObjectForm/><DefaultListForm/><DefaultChoiceForm/><AuxiliaryObjectForm/><AuxiliaryListForm/><AuxiliaryChoiceForm/>
\t\t\t<Posting>{posting_value}</Posting><RealTimePosting>Deny</RealTimePosting>
\t\t\t<RegisterRecordsDeletion>AutoDelete</RegisterRecordsDeletion><RegisterRecordsWritingOnPost>WriteSelected</RegisterRecordsWritingOnPost>
\t\t\t<SequenceFilling>AutoFill</SequenceFilling><RegisterRecords/>
\t\t\t<PostInPrivilegedMode>true</PostInPrivilegedMode><UnpostInPrivilegedMode>true</UnpostInPrivilegedMode>
\t\t\t<IncludeHelpInContents>false</IncludeHelpInContents><DataLockFields/><DataLockControlMode>Managed</DataLockControlMode>
\t\t\t<FullTextSearch>Use</FullTextSearch><ObjectPresentation/><ExtendedObjectPresentation/><ListPresentation/>
\t\t\t<ExtendedListPresentation/><Explanation/><ChoiceHistoryOnInput>Auto</ChoiceHistoryOnInput>
\t\t\t<DataHistory>DontUse</DataHistory><UpdateDataHistoryImmediatelyAfterWrite>false</UpdateDataHistoryImmediatelyAfterWrite>
\t\t\t<ExecuteAfterWriteDataHistoryVersionProcessing>false</ExecuteAfterWriteDataHistoryVersionProcessing>
\t\t</Properties>
\t\t<ChildObjects/>
\t</Document>
</MetaDataObject>
'''

    def create_catalog(self, name: str, *, synonym: str | None = None, hierarchical: bool = False) -> SemanticChange:
        name = self._identifier(name, "catalog name")
        path = self._object_path("catalog", name)
        if self.workspace.resolve(path).exists():
            raise SemanticMetadataError(f"Catalog already exists: {name}")
        snapshot = self.snapshots.create(["Configuration.xml", path])
        self.workspace.write_text(path, self._catalog_xml(name, synonym or name, hierarchical=hierarchical))
        self._register_root_child("Catalog", name)
        return self._change(snapshot, f"Created catalog metadata Catalog.{name}")

    def create_document(self, name: str, *, synonym: str | None = None, posting: bool = False) -> SemanticChange:
        name = self._identifier(name, "document name")
        path = self._object_path("document", name)
        if self.workspace.resolve(path).exists():
            raise SemanticMetadataError(f"Document already exists: {name}")
        snapshot = self.snapshots.create(["Configuration.xml", path])
        self.workspace.write_text(path, self._document_xml(name, synonym or name, posting=posting))
        self._register_root_child("Document", name)
        return self._change(snapshot, f"Created document metadata Document.{name}")

    @staticmethod
    def _type_fragment(value_type: str, *, string_length: int, digits: int, fraction_digits: int) -> str:
        normalized = value_type.strip()
        primitive = normalized.lower()
        if primitive in {"string", "строка"}:
            if not 1 <= string_length <= 1024:
                raise SemanticMetadataError("string_length must be between 1 and 1024")
            return (
                "<v8:Type>xs:string</v8:Type>"
                f"<v8:StringQualifiers><v8:Length>{string_length}</v8:Length>"
                "<v8:AllowedLength>Variable</v8:AllowedLength></v8:StringQualifiers>"
            )
        if primitive in {"number", "число"}:
            if not 1 <= digits <= 38 or not 0 <= fraction_digits < digits:
                raise SemanticMetadataError("Invalid number qualifiers")
            return (
                "<v8:Type>xs:decimal</v8:Type>"
                f"<v8:NumberQualifiers><v8:Digits>{digits}</v8:Digits>"
                f"<v8:FractionDigits>{fraction_digits}</v8:FractionDigits>"
                "<v8:AllowedSign>Any</v8:AllowedSign></v8:NumberQualifiers>"
            )
        if primitive in {"boolean", "bool", "булево"}:
            return "<v8:Type>xs:boolean</v8:Type>"
        if primitive in {"date", "datetime", "дата"}:
            return "<v8:Type>xs:dateTime</v8:Type>"
        reference = REFERENCE_TYPE_RE.fullmatch(normalized)
        if reference:
            return f"<v8:Type>cfg:{escape(normalized)}</v8:Type>"
        raise SemanticMetadataError(f"Unsupported attribute type: {value_type!r}")

    def add_attribute(
        self,
        kind: str,
        object_name: str,
        attribute_name: str,
        *,
        value_type: str = "string",
        synonym: str | None = None,
        string_length: int = 100,
        digits: int = 15,
        fraction_digits: int = 2,
    ) -> SemanticChange:
        normalized_kind = kind.strip().lower().replace("-", "_")
        folder, _ = self._kind(normalized_kind)
        object_name = self._identifier(object_name, "object name")
        attribute_name = self._identifier(attribute_name, "attribute name")
        path = Path(folder) / f"{object_name}.xml"
        target = self.workspace.resolve(path)
        if not target.exists():
            raise SemanticMetadataError(f"Metadata object was not found: {normalized_kind}.{object_name}")
        text = target.read_text(encoding="utf-8-sig")
        if re.search(rf"<Attribute\b[^>]*>.*?<Name>{re.escape(attribute_name)}</Name>", text, re.S):
            raise SemanticMetadataError(f"Attribute already exists: {attribute_name}")

        type_xml = self._type_fragment(
            value_type,
            string_length=string_length,
            digits=digits,
            fraction_digits=fraction_digits,
        )
        if "cfg:" in type_xml and "xmlns:cfg=" not in text:
            text = text.replace(
                '<MetaDataObject ',
                '<MetaDataObject xmlns:cfg="http://v8.1c.ru/8.1/data/enterprise/current-config" ',
                1,
            )

        synonym_xml = escape(synonym or attribute_name)
        use_property = "\n\t\t\t\t\t<Use>ForItem</Use>" if normalized_kind == "catalog" else ""
        snippet = f'''<Attribute uuid="{uuid.uuid4()}">
\t\t\t\t<Properties>
\t\t\t\t\t<Name>{attribute_name}</Name>
\t\t\t\t\t<Synonym><v8:item><v8:lang>ru</v8:lang><v8:content>{synonym_xml}</v8:content></v8:item></Synonym>
\t\t\t\t\t<Comment/><Type>{type_xml}</Type><PasswordMode>false</PasswordMode><Format/><EditFormat/><ToolTip/>
\t\t\t\t\t<MarkNegatives>false</MarkNegatives><Mask/><MultiLine>false</MultiLine><ExtendedEdit>false</ExtendedEdit>
\t\t\t\t\t<MinValue xsi:nil="true"/><MaxValue xsi:nil="true"/><FillFromFillingValue>true</FillFromFillingValue>
\t\t\t\t\t<FillValue xsi:nil="true"/><FillChecking>DontCheck</FillChecking><ChoiceFoldersAndItems>Items</ChoiceFoldersAndItems>
\t\t\t\t\t<ChoiceParameterLinks/><ChoiceParameters/><QuickChoice>Auto</QuickChoice><CreateOnInput>Auto</CreateOnInput>
\t\t\t\t\t<ChoiceForm/><LinkByType/><ChoiceHistoryOnInput>Auto</ChoiceHistoryOnInput>{use_property}
\t\t\t\t\t<Indexing>DontIndex</Indexing><FullTextSearch>Use</FullTextSearch><DataHistory>Use</DataHistory>
\t\t\t\t</Properties>
\t\t\t</Attribute>'''

        snapshot = self.snapshots.create([path])
        self_closing = re.search(r"(?m)^(?P<i>[ \t]*)<ChildObjects\s*/>\s*$", text)
        if self_closing:
            indent = self_closing.group("i")
            block = f"{indent}<ChildObjects>\n{indent}\t{snippet}\n{indent}</ChildObjects>"
            text = text[: self_closing.start()] + block + text[self_closing.end() :]
        else:
            closings = list(re.finditer(r"(?m)^(?P<i>[ \t]*)</ChildObjects>\s*$", text))
            if not closings:
                raise SemanticMetadataError(f"{path} has no ChildObjects section")
            closing = closings[-1]
            indent = closing.group("i")
            text = text[: closing.start()] + f"{indent}\t{snippet}\n" + text[closing.start() :]
        target.write_text(text, encoding="utf-8")
        return self._change(snapshot, f"Added attribute {attribute_name} to {normalized_kind}.{object_name}")

    def ensure_module(
        self,
        kind: str,
        object_name: str,
        *,
        module: str = "object",
        content: str = "",
    ) -> SemanticChange:
        folder, _ = self._kind(kind)
        object_name = self._identifier(object_name, "object name")
        module_key = module.strip().lower()
        if module_key not in self._MODULES:
            raise SemanticMetadataError(f"Unsupported module kind: {module!r}")
        object_path = Path(folder) / f"{object_name}.xml"
        if not self.workspace.resolve(object_path).exists():
            raise SemanticMetadataError(f"Metadata object was not found: {kind}.{object_name}")
        module_path = Path(folder) / object_name / "Ext" / self._MODULES[module_key]
        if self.workspace.resolve(module_path).exists():
            raise SemanticMetadataError(f"Module already exists: {module_path}")
        snapshot = self.snapshots.create([module_path])
        self.workspace.write_text(module_path, content)
        return self._change(snapshot, f"Created module {module_path}")

    @staticmethod
    def _change(snapshot: SnapshotInfo, summary: str) -> SemanticChange:
        return SemanticChange(
            summary=summary,
            snapshot_id=snapshot.snapshot_id,
            paths=snapshot.paths,
            requires_reload=True,
        )
