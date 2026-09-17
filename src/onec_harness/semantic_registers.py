from __future__ import annotations

import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from onec_harness.semantic import NAMESPACE_LINE, SemanticChange, SemanticMetadataError
from onec_harness.semantic_extra import ExtendedMetadataEditor


class RegisterMetadataEditor(ExtendedMetadataEditor):
    """Deterministic high-level builders for common 1C register metadata."""

    _KINDS = {
        **ExtendedMetadataEditor._KINDS,
        "information_register": ("InformationRegisters", "InformationRegister"),
        "accumulation_register": ("AccumulationRegisters", "AccumulationRegister"),
    }

    @staticmethod
    def _generated_types(prefix: str, name: str, categories: Sequence[str], ids: Sequence[str]) -> str:
        lines: list[str] = []
        for index, category in enumerate(categories):
            type_id = ids[index * 2]
            value_id = ids[index * 2 + 1]
            lines.extend(
                [
                    f'\t\t\t<xr:GeneratedType name="{prefix}{category}.{name}" category="{category}">',
                    f"\t\t\t\t<xr:TypeId>{type_id}</xr:TypeId>",
                    f"\t\t\t\t<xr:ValueId>{value_id}</xr:ValueId>",
                    "\t\t\t</xr:GeneratedType>",
                ]
            )
        return "\n".join(lines)

    def _register_field_xml(
        self,
        tag: str,
        raw: dict[str, Any],
        *,
        indent: str = "\t\t\t",
        dimension: bool = False,
    ) -> str:
        name = self._identifier(str(raw.get("name", "")), f"{tag.lower()} name")
        synonym = escape(str(raw.get("synonym") or name))
        value_type = str(raw.get("value_type", "string"))
        type_xml = self._type_fragment(
            value_type,
            string_length=int(raw.get("string_length", 100)),
            digits=int(raw.get("digits", 15)),
            fraction_digits=int(raw.get("fraction_digits", 2)),
        )
        dimension_xml = ""
        if dimension:
            dimension_xml = (
                f"\n{indent}\t\t<Master>{str(bool(raw.get('master', False))).lower()}</Master>"
                f"\n{indent}\t\t<MainFilter>{str(bool(raw.get('main_filter', False))).lower()}</MainFilter>"
                f"\n{indent}\t\t<DenyIncompleteValues>{str(bool(raw.get('deny_incomplete_values', False))).lower()}"
                "</DenyIncompleteValues>"
            )
        return f'''{indent}<{tag} uuid="{uuid.uuid4()}">
{indent}\t<Properties>
{indent}\t\t<Name>{name}</Name>
{indent}\t\t<Synonym><v8:item><v8:lang>ru</v8:lang><v8:content>{synonym}</v8:content></v8:item></Synonym>
{indent}\t\t<Comment/><Type>{type_xml}</Type><PasswordMode>false</PasswordMode><Format/><EditFormat/><ToolTip/>
{indent}\t\t<MarkNegatives>false</MarkNegatives><Mask/><MultiLine>false</MultiLine><ExtendedEdit>false</ExtendedEdit>
{indent}\t\t<MinValue xsi:nil="true"/><MaxValue xsi:nil="true"/><FillFromFillingValue>false</FillFromFillingValue>
{indent}\t\t<FillValue xsi:nil="true"/><FillChecking>DontCheck</FillChecking><ChoiceFoldersAndItems>Items</ChoiceFoldersAndItems>
{indent}\t\t<ChoiceParameterLinks/><ChoiceParameters/><QuickChoice>Auto</QuickChoice><CreateOnInput>Auto</CreateOnInput>
{indent}\t\t<ChoiceForm/><LinkByType/><ChoiceHistoryOnInput>Auto</ChoiceHistoryOnInput>{dimension_xml}
{indent}\t\t<Indexing>DontIndex</Indexing><FullTextSearch>Use</FullTextSearch><DataHistory>Use</DataHistory>
{indent}\t</Properties>
{indent}</{tag}>'''

    def _register_children(
        self,
        dimensions: Sequence[dict[str, Any]],
        resources: Sequence[dict[str, Any]],
        attributes: Sequence[dict[str, Any]],
    ) -> str:
        blocks: list[str] = []
        blocks.extend(self._register_field_xml("Dimension", item, dimension=True) for item in dimensions)
        blocks.extend(self._register_field_xml("Resource", item) for item in resources)
        blocks.extend(self._register_field_xml("Attribute", item) for item in attributes)
        if not blocks:
            return "\t\t<ChildObjects/>"
        return "\t\t<ChildObjects>\n" + "\n".join(blocks) + "\n\t\t</ChildObjects>"

    def _standard_attributes(self, names: Sequence[str]) -> str:
        return "\n".join(self._standard_attribute(name, indent="\t\t\t\t") for name in names)

    def create_information_register(
        self,
        name: str,
        *,
        synonym: str | None = None,
        periodicity: str = "Nonperiodical",
        write_mode: str = "Independent",
        dimensions: Sequence[dict[str, Any]] = (),
        resources: Sequence[dict[str, Any]] = (),
        attributes: Sequence[dict[str, Any]] = (),
        main_filter_on_period: bool = False,
    ) -> SemanticChange:
        name = self._identifier(name, "information register name")
        allowed_periodicity = {"Nonperiodical", "Second", "Day", "Month", "Quarter", "Year", "RecorderPosition"}
        if periodicity not in allowed_periodicity:
            raise SemanticMetadataError(f"Unsupported information register periodicity: {periodicity}")
        if write_mode not in {"Independent", "RecorderSubordinate"}:
            raise SemanticMetadataError(f"Unsupported information register write mode: {write_mode}")
        path = Path("InformationRegisters") / f"{name}.xml"
        if self.workspace.resolve(path).exists():
            raise SemanticMetadataError(f"Information register already exists: {name}")

        generated_ids = self._new_ids(14)
        categories = ("Record", "Manager", "Selection", "List", "RecordSet", "RecordKey", "RecordManager")
        generated = self._generated_types("InformationRegister", name, categories, generated_ids)
        standard = self._standard_attributes(("Active", "LineNumber", "Recorder", "Period"))
        children = self._register_children(dimensions, resources, attributes)
        version = self._format_version()
        root_uuid = uuid.uuid4()
        synonym_text = escape(synonym or name)
        xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<MetaDataObject {NAMESPACE_LINE} version="{version}">
\t<InformationRegister uuid="{root_uuid}">
\t\t<InternalInfo>
{generated}
\t\t</InternalInfo>
\t\t<Properties>
\t\t\t<Name>{name}</Name>
\t\t\t<Synonym><v8:item><v8:lang>ru</v8:lang><v8:content>{synonym_text}</v8:content></v8:item></Synonym>
\t\t\t<Comment/><UseStandardCommands>true</UseStandardCommands><EditType>InDialog</EditType>
\t\t\t<DefaultRecordForm/><DefaultListForm/><AuxiliaryRecordForm/><AuxiliaryListForm/>
\t\t\t<StandardAttributes>
{standard}
\t\t\t</StandardAttributes>
\t\t\t<InformationRegisterPeriodicity>{periodicity}</InformationRegisterPeriodicity>
\t\t\t<WriteMode>{write_mode}</WriteMode>
\t\t\t<MainFilterOnPeriod>{str(main_filter_on_period).lower()}</MainFilterOnPeriod>
\t\t\t<IncludeHelpInContents>false</IncludeHelpInContents><DataLockControlMode>Managed</DataLockControlMode>
\t\t\t<FullTextSearch>Use</FullTextSearch><EnableTotalsSliceFirst>false</EnableTotalsSliceFirst>
\t\t\t<EnableTotalsSliceLast>false</EnableTotalsSliceLast><RecordPresentation/><ExtendedRecordPresentation/>
\t\t\t<ListPresentation/><ExtendedListPresentation/><Explanation/><DataHistory>DontUse</DataHistory>
\t\t\t<UpdateDataHistoryImmediatelyAfterWrite>false</UpdateDataHistoryImmediatelyAfterWrite>
\t\t\t<ExecuteAfterWriteDataHistoryVersionProcessing>false</ExecuteAfterWriteDataHistoryVersionProcessing>
\t\t</Properties>
{children}
\t</InformationRegister>
</MetaDataObject>
'''
        snapshot = self.snapshots.create(["Configuration.xml", path])
        self.workspace.write_text(path, xml)
        self._register_root_child("InformationRegister", name)
        return self._change(snapshot, f"Created information register InformationRegister.{name}")

    def create_accumulation_register(
        self,
        name: str,
        *,
        synonym: str | None = None,
        register_type: str = "Balance",
        dimensions: Sequence[dict[str, Any]] = (),
        resources: Sequence[dict[str, Any]] = (),
        attributes: Sequence[dict[str, Any]] = (),
        enable_totals_splitting: bool = True,
    ) -> SemanticChange:
        name = self._identifier(name, "accumulation register name")
        aliases = {"balance": "Balance", "balances": "Balance", "turnover": "Turnovers", "turnovers": "Turnovers"}
        normalized = aliases.get(register_type.strip().lower())
        if normalized is None:
            raise SemanticMetadataError("Accumulation register type must be Balance/Balances or Turnovers")
        path = Path("AccumulationRegisters") / f"{name}.xml"
        if self.workspace.resolve(path).exists():
            raise SemanticMetadataError(f"Accumulation register already exists: {name}")

        generated_ids = self._new_ids(12)
        categories = ("Record", "Manager", "Selection", "List", "RecordSet", "RecordKey")
        generated = self._generated_types("AccumulationRegister", name, categories, generated_ids)
        standard_names = ("RecordType", "Active", "LineNumber", "Recorder", "Period") if normalized == "Balance" else (
            "Active",
            "LineNumber",
            "Recorder",
            "Period",
        )
        standard = self._standard_attributes(standard_names)
        children = self._register_children(dimensions, resources, attributes)
        version = self._format_version()
        root_uuid = uuid.uuid4()
        synonym_text = escape(synonym or name)
        xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<MetaDataObject {NAMESPACE_LINE} version="{version}">
\t<AccumulationRegister uuid="{root_uuid}">
\t\t<InternalInfo>
{generated}
\t\t</InternalInfo>
\t\t<Properties>
\t\t\t<Name>{name}</Name>
\t\t\t<Synonym><v8:item><v8:lang>ru</v8:lang><v8:content>{synonym_text}</v8:content></v8:item></Synonym>
\t\t\t<Comment/><UseStandardCommands>true</UseStandardCommands><DefaultListForm/><AuxiliaryListForm/>
\t\t\t<RegisterType>{normalized}</RegisterType><IncludeHelpInContents>false</IncludeHelpInContents>
\t\t\t<StandardAttributes>
{standard}
\t\t\t</StandardAttributes>
\t\t\t<DataLockControlMode>Managed</DataLockControlMode><FullTextSearch>Use</FullTextSearch>
\t\t\t<EnableTotalsSplitting>{str(enable_totals_splitting).lower()}</EnableTotalsSplitting>
\t\t\t<ListPresentation/><ExtendedListPresentation/><Explanation/>
\t\t</Properties>
{children}
\t</AccumulationRegister>
</MetaDataObject>
'''
        snapshot = self.snapshots.create(["Configuration.xml", path])
        self.workspace.write_text(path, xml)
        self._register_root_child("AccumulationRegister", name)
        return self._change(snapshot, f"Created accumulation register AccumulationRegister.{name}")
