from __future__ import annotations

import re
import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from onec_harness.semantic import NAMESPACE_LINE, MetadataEditor, SemanticChange, SemanticMetadataError


class ExtendedMetadataEditor(MetadataEditor):
    """Additional deterministic semantic operations for common 1C metadata structures."""

    _KINDS = {
        **MetadataEditor._KINDS,
        "enum": ("Enums", "Enum"),
    }

    @staticmethod
    def _standard_attribute(name: str, *, indent: str) -> str:
        return f'''{indent}<xr:StandardAttribute name="{name}">
{indent}\t<xr:LinkByType/><xr:FillChecking>DontCheck</xr:FillChecking><xr:MultiLine>false</xr:MultiLine>
{indent}\t<xr:FillFromFillingValue>false</xr:FillFromFillingValue><xr:CreateOnInput>Auto</xr:CreateOnInput>
{indent}\t<xr:MaxValue xsi:nil="true"/><xr:ToolTip/><xr:ExtendedEdit>false</xr:ExtendedEdit><xr:Format/>
{indent}\t<xr:ChoiceForm/><xr:QuickChoice>Auto</xr:QuickChoice><xr:ChoiceHistoryOnInput>Auto</xr:ChoiceHistoryOnInput>
{indent}\t<xr:EditFormat/><xr:PasswordMode>false</xr:PasswordMode><xr:DataHistory>Use</xr:DataHistory>
{indent}\t<xr:MarkNegatives>false</xr:MarkNegatives><xr:MinValue xsi:nil="true"/><xr:Synonym/><xr:Comment/>
{indent}\t<xr:FullTextSearch>Use</xr:FullTextSearch><xr:ChoiceParameterLinks/><xr:FillValue xsi:nil="true"/>
{indent}\t<xr:Mask/><xr:ChoiceParameters/>
{indent}</xr:StandardAttribute>'''

    @staticmethod
    def _enum_value_xml(name: str, synonym: str | None = None, *, indent: str = "\t\t\t") -> str:
        name = ExtendedMetadataEditor._identifier(name, "enum value name")
        synonym_xml = escape(synonym or name)
        return f'''{indent}<EnumValue uuid="{uuid.uuid4()}">
{indent}\t<Properties>
{indent}\t\t<Name>{name}</Name>
{indent}\t\t<Synonym><v8:item><v8:lang>ru</v8:lang><v8:content>{synonym_xml}</v8:content></v8:item></Synonym>
{indent}\t\t<Comment/>
{indent}\t</Properties>
{indent}</EnumValue>'''

    def _enum_xml(self, name: str, synonym: str, values: Sequence[str | dict[str, Any]]) -> str:
        ids = self._new_ids(7)
        version = self._format_version()
        value_blocks: list[str] = []
        for raw in values:
            if isinstance(raw, str):
                value_blocks.append(self._enum_value_xml(raw))
            elif isinstance(raw, dict):
                value_blocks.append(
                    self._enum_value_xml(
                        str(raw.get("name", "")),
                        str(raw["synonym"]) if raw.get("synonym") is not None else None,
                    )
                )
            else:
                raise SemanticMetadataError("Enum values must be strings or objects")
        values_xml = "\n".join(value_blocks)
        standard_order = self._standard_attribute("Order", indent="\t\t\t\t")
        standard_ref = self._standard_attribute("Ref", indent="\t\t\t\t")
        child_objects = f"\n{values_xml}\n\t\t" if values_xml else ""
        return f'''<?xml version="1.0" encoding="UTF-8"?>
<MetaDataObject {NAMESPACE_LINE} version="{version}">
\t<Enum uuid="{ids[0]}">
\t\t<InternalInfo>
\t\t\t<xr:GeneratedType name="EnumRef.{name}" category="Ref"><xr:TypeId>{ids[1]}</xr:TypeId><xr:ValueId>{ids[2]}</xr:ValueId></xr:GeneratedType>
\t\t\t<xr:GeneratedType name="EnumManager.{name}" category="Manager"><xr:TypeId>{ids[3]}</xr:TypeId><xr:ValueId>{ids[4]}</xr:ValueId></xr:GeneratedType>
\t\t\t<xr:GeneratedType name="EnumList.{name}" category="List"><xr:TypeId>{ids[5]}</xr:TypeId><xr:ValueId>{ids[6]}</xr:ValueId></xr:GeneratedType>
\t\t</InternalInfo>
\t\t<Properties>
\t\t\t<Name>{name}</Name>
\t\t\t<Synonym><v8:item><v8:lang>ru</v8:lang><v8:content>{escape(synonym)}</v8:content></v8:item></Synonym>
\t\t\t<Comment/><UseStandardCommands>false</UseStandardCommands>
\t\t\t<StandardAttributes>
{standard_order}
{standard_ref}
\t\t\t</StandardAttributes>
\t\t\t<Characteristics/><QuickChoice>true</QuickChoice><ChoiceMode>BothWays</ChoiceMode>
\t\t\t<DefaultListForm/><DefaultChoiceForm/><AuxiliaryListForm/><AuxiliaryChoiceForm/>
\t\t\t<ListPresentation/><ExtendedListPresentation/><Explanation/><ChoiceHistoryOnInput>Auto</ChoiceHistoryOnInput>
\t\t</Properties>
\t\t<ChildObjects>{child_objects}</ChildObjects>
\t</Enum>
</MetaDataObject>
'''

    def create_enum(
        self,
        name: str,
        *,
        synonym: str | None = None,
        values: Sequence[str | dict[str, Any]] = (),
    ) -> SemanticChange:
        name = self._identifier(name, "enum name")
        path = self._object_path("enum", name)
        if self.workspace.resolve(path).exists():
            raise SemanticMetadataError(f"Enum already exists: {name}")
        snapshot = self.snapshots.create(["Configuration.xml", path])
        self.workspace.write_text(path, self._enum_xml(name, synonym or name, values))
        self._register_root_child("Enum", name)
        return self._change(snapshot, f"Created enum metadata Enum.{name}")

    def add_enum_value(self, enum_name: str, value_name: str, *, synonym: str | None = None) -> SemanticChange:
        enum_name = self._identifier(enum_name, "enum name")
        value_name = self._identifier(value_name, "enum value name")
        path = Path("Enums") / f"{enum_name}.xml"
        target = self.workspace.resolve(path)
        if not target.exists():
            raise SemanticMetadataError(f"Enum was not found: {enum_name}")
        text = target.read_text(encoding="utf-8-sig")
        if re.search(rf"<EnumValue\b[^>]*>.*?<Name>{re.escape(value_name)}</Name>", text, re.S):
            raise SemanticMetadataError(f"Enum value already exists: {value_name}")
        snapshot = self.snapshots.create([path])
        snippet = self._enum_value_xml(value_name, synonym)
        self_closing = re.search(r"(?m)^(?P<i>[ \t]*)<ChildObjects\s*/>\s*$", text)
        if self_closing:
            indent = self_closing.group("i")
            replacement = f"{indent}<ChildObjects>\n{snippet}\n{indent}</ChildObjects>"
            text = text[: self_closing.start()] + replacement + text[self_closing.end() :]
        else:
            closings = list(re.finditer(r"(?m)^(?P<i>[ \t]*)</ChildObjects>\s*$", text))
            if not closings:
                raise SemanticMetadataError(f"{path} has no ChildObjects section")
            closing = closings[-1]
            text = text[: closing.start()] + f"{snippet}\n" + text[closing.start() :]
        target.write_text(text, encoding="utf-8")
        return self._change(snapshot, f"Added enum value {value_name} to Enum.{enum_name}")

    def _tabular_column_xml(
        self,
        column: dict[str, Any],
        *,
        indent: str,
        catalog_use: bool,
    ) -> str:
        name = self._identifier(str(column.get("name", "")), "tabular column name")
        synonym = escape(str(column.get("synonym") or name))
        value_type = str(column.get("value_type", "string"))
        type_xml = self._type_fragment(
            value_type,
            string_length=int(column.get("string_length", 100)),
            digits=int(column.get("digits", 15)),
            fraction_digits=int(column.get("fraction_digits", 2)),
        )
        use_xml = f"\n{indent}\t\t<Use>ForItem</Use>" if catalog_use else ""
        return f'''{indent}<Attribute uuid="{uuid.uuid4()}">
{indent}\t<Properties>
{indent}\t\t<Name>{name}</Name>
{indent}\t\t<Synonym><v8:item><v8:lang>ru</v8:lang><v8:content>{synonym}</v8:content></v8:item></Synonym>
{indent}\t\t<Comment/><Type>{type_xml}</Type><PasswordMode>false</PasswordMode><Format/><EditFormat/><ToolTip/>
{indent}\t\t<MarkNegatives>false</MarkNegatives><Mask/><MultiLine>false</MultiLine><ExtendedEdit>false</ExtendedEdit>
{indent}\t\t<MinValue xsi:nil="true"/><MaxValue xsi:nil="true"/><FillFromFillingValue>false</FillFromFillingValue>
{indent}\t\t<FillValue xsi:nil="true"/><FillChecking>DontCheck</FillChecking><ChoiceFoldersAndItems>Items</ChoiceFoldersAndItems>
{indent}\t\t<ChoiceParameterLinks/><ChoiceParameters/><QuickChoice>Auto</QuickChoice><CreateOnInput>Auto</CreateOnInput>
{indent}\t\t<ChoiceForm/><LinkByType/><ChoiceHistoryOnInput>Auto</ChoiceHistoryOnInput>{use_xml}
{indent}\t\t<Indexing>DontIndex</Indexing><FullTextSearch>Use</FullTextSearch><DataHistory>Use</DataHistory>
{indent}\t</Properties>
{indent}</Attribute>'''

    def add_tabular_section(
        self,
        kind: str,
        object_name: str,
        section_name: str,
        *,
        synonym: str | None = None,
        columns: Sequence[dict[str, Any]] = (),
    ) -> SemanticChange:
        normalized_kind = kind.strip().lower().replace("-", "_")
        if normalized_kind not in {"catalog", "document"}:
            raise SemanticMetadataError("Tabular sections are currently supported for catalogs and documents")
        folder, tag = self._kind(normalized_kind)
        object_name = self._identifier(object_name, "object name")
        section_name = self._identifier(section_name, "tabular section name")
        path = Path(folder) / f"{object_name}.xml"
        target = self.workspace.resolve(path)
        if not target.exists():
            raise SemanticMetadataError(f"Metadata object was not found: {normalized_kind}.{object_name}")
        text = target.read_text(encoding="utf-8-sig")
        if re.search(rf"<TabularSection\b[^>]*>.*?<Name>{re.escape(section_name)}</Name>", text, re.S):
            raise SemanticMetadataError(f"Tabular section already exists: {section_name}")

        ids = [str(uuid.uuid4()) for _ in range(5)]
        prefix = tag
        section_synonym = escape(synonym or section_name)
        standard_line = self._standard_attribute("LineNumber", indent="\t\t\t\t\t\t")
        column_xml = "\n".join(
            self._tabular_column_xml(column, indent="\t\t\t\t\t", catalog_use=normalized_kind == "catalog")
            for column in columns
        )
        use_xml = "\n\t\t\t\t\t<Use>ForItem</Use>" if normalized_kind == "catalog" else ""
        child_xml = f"\n{column_xml}\n\t\t\t\t" if column_xml else ""
        snippet = f'''<TabularSection uuid="{ids[0]}">
\t\t\t\t<InternalInfo>
\t\t\t\t\t<xr:GeneratedType name="{prefix}TabularSection.{object_name}.{section_name}" category="TabularSection"><xr:TypeId>{ids[1]}</xr:TypeId><xr:ValueId>{ids[2]}</xr:ValueId></xr:GeneratedType>
\t\t\t\t\t<xr:GeneratedType name="{prefix}TabularSectionRow.{object_name}.{section_name}" category="TabularSectionRow"><xr:TypeId>{ids[3]}</xr:TypeId><xr:ValueId>{ids[4]}</xr:ValueId></xr:GeneratedType>
\t\t\t\t</InternalInfo>
\t\t\t\t<Properties>
\t\t\t\t\t<Name>{section_name}</Name>
\t\t\t\t\t<Synonym><v8:item><v8:lang>ru</v8:lang><v8:content>{section_synonym}</v8:content></v8:item></Synonym>
\t\t\t\t\t<Comment/><ToolTip/><FillChecking>DontCheck</FillChecking>
\t\t\t\t\t<StandardAttributes>
{standard_line}
\t\t\t\t\t</StandardAttributes>{use_xml}
\t\t\t\t</Properties>
\t\t\t\t<ChildObjects>{child_xml}</ChildObjects>
\t\t\t</TabularSection>'''

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
        return self._change(snapshot, f"Added tabular section {section_name} to {normalized_kind}.{object_name}")
