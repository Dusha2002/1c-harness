from pathlib import Path

from onec_harness.metadata import ConfigurationIndex
from onec_harness.semantic import MetadataEditor
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
