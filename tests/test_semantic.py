from pathlib import Path

from onec_harness.metadata import ConfigurationIndex
from onec_harness.semantic import MetadataEditor
from onec_harness.semantic_extra import ExtendedMetadataEditor
from onec_harness.workspace import Workspace


CONFIG = '''<?xml version="1.0" encoding="UTF-8"?>
<MetaDataObject xmlns="http://v8.1c.ru/8.3/MDClasses" version="2.17">
  <Configuration uuid="00000000-0000-0000-0000-000000000001">
    <Properties><Name>HarnessTest</Name></Properties>
    <ChildObjects/>
  </Configuration>
</MetaDataObject>
'''


def test_create_catalog_registers_object_and_snapshot(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path)
    workspace.write_text("Configuration.xml", CONFIG)
    editor = MetadataEditor(workspace)

    change = editor.create_catalog("Оборудование", synonym="Оборудование")

    catalog = workspace.read_text("Catalogs/Оборудование.xml")
    configuration = workspace.read_text("Configuration.xml")
    assert "<Name>Оборудование</Name>" in catalog
    assert "<Catalog>Оборудование</Catalog>" in configuration
    assert change.snapshot_id
    assert (tmp_path / ".onec-harness" / "snapshots" / f"{change.snapshot_id}.json").exists()
    index = ConfigurationIndex.build(tmp_path)
    assert any(item.kind == "catalog" and item.name == "Оборудование" for item in index.objects)


def test_add_attribute_to_created_catalog(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path)
    workspace.write_text("Configuration.xml", CONFIG)
    editor = MetadataEditor(workspace)
    editor.create_catalog("Оборудование")

    change = editor.add_attribute(
        "catalog",
        "Оборудование",
        "СерийныйНомер",
        value_type="string",
        string_length=80,
    )

    text = workspace.read_text("Catalogs/Оборудование.xml")
    assert "<Name>СерийныйНомер</Name>" in text
    assert "<v8:Length>80</v8:Length>" in text
    assert change.snapshot_id


def test_create_document_can_allow_posting(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path)
    workspace.write_text("Configuration.xml", CONFIG)

    MetadataEditor(workspace).create_document("Заявка", posting=True)

    text = workspace.read_text("Documents/Заявка.xml")
    assert "<Posting>Allow</Posting>" in text
    assert "<Document>Заявка</Document>" in workspace.read_text("Configuration.xml")


def test_create_enum_with_values_is_indexed(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path)
    workspace.write_text("Configuration.xml", CONFIG)
    editor = ExtendedMetadataEditor(workspace)

    change = editor.create_enum("Статусы", values=["Новый", {"name": "Закрыт", "synonym": "Закрыт"}])

    text = workspace.read_text("Enums/Статусы.xml")
    assert "<Enum " in text
    assert "<Name>Новый</Name>" in text
    assert "<Name>Закрыт</Name>" in text
    assert "<Enum>Статусы</Enum>" in workspace.read_text("Configuration.xml")
    assert change.snapshot_id
    index = ConfigurationIndex.build(tmp_path)
    assert any(item.kind == "enum" and item.name == "Статусы" for item in index.objects)


def test_add_enum_value(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path)
    workspace.write_text("Configuration.xml", CONFIG)
    editor = ExtendedMetadataEditor(workspace)
    editor.create_enum("Статусы", values=["Новый"])

    editor.add_enum_value("Статусы", "Отменен", synonym="Отменен")

    text = workspace.read_text("Enums/Статусы.xml")
    assert "<Name>Отменен</Name>" in text


def test_add_typed_tabular_section_to_document(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path)
    workspace.write_text("Configuration.xml", CONFIG)
    editor = ExtendedMetadataEditor(workspace)
    editor.create_document("Заявка")

    change = editor.add_tabular_section(
        "document",
        "Заявка",
        "Товары",
        columns=[
            {"name": "Товар", "value_type": "CatalogRef.Товары"},
            {"name": "Количество", "value_type": "number", "digits": 15, "fraction_digits": 3},
        ],
    )

    text = workspace.read_text("Documents/Заявка.xml")
    assert "DocumentTabularSection.Заявка.Товары" in text
    assert "DocumentTabularSectionRow.Заявка.Товары" in text
    assert "<Name>Товар</Name>" in text
    assert "cfg:CatalogRef.Товары" in text
    assert "<Name>Количество</Name>" in text
    assert "<v8:FractionDigits>3</v8:FractionDigits>" in text
    assert change.snapshot_id
