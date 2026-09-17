from pathlib import Path

from onec_harness.extensions import ExtensionSourceManager
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

EXTENSION = '''<?xml version="1.0" encoding="UTF-8"?>
<MetaDataObject xmlns="http://v8.1c.ru/8.3/MDClasses" version="2.17">
  <Configuration uuid="00000000-0000-0000-0000-000000000099">
    <Properties>
      <ObjectBelonging>Adopted</ObjectBelonging>
      <Name>HarnessExt</Name>
      <ConfigurationExtensionPurpose>Patch</ConfigurationExtensionPurpose>
      <NamePrefix>Harness_</NamePrefix>
    </Properties>
    <ChildObjects/>
  </Configuration>
</MetaDataObject>
'''


def _workspace(tmp_path: Path) -> Workspace:
    workspace = Workspace(tmp_path)
    workspace.write_text("Configuration.xml", CONFIG)
    workspace.write_text("Extensions/HarnessExt/Configuration.xml", EXTENSION)
    return workspace


def test_borrow_catalog_into_extension(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    MetadataEditor(workspace).create_catalog("Товары")
    manager = ExtensionSourceManager(workspace)

    change = manager.borrow_object("HarnessExt", "catalog", "Товары")

    text = workspace.read_text("Extensions/HarnessExt/Catalogs/Товары.xml")
    config = workspace.read_text("Extensions/HarnessExt/Configuration.xml")
    assert "<ObjectBelonging>Adopted</ObjectBelonging>" in text
    assert "<ExtendedConfigurationObject>" in text
    assert "CatalogObject.Товары" in text
    assert "<Catalog>Товары</Catalog>" in config
    assert change.snapshot_id


def test_patch_borrowed_object_method(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    MetadataEditor(workspace).create_document("Заказ")
    manager = ExtensionSourceManager(workspace)
    manager.borrow_object("HarnessExt", "document", "Заказ")

    change = manager.patch_method(
        "HarnessExt",
        "document",
        "Заказ",
        "ОбработкаПроведения",
        interceptor="Before",
        module="object",
        handler_name="Harness_ОбработкаПроведения",
        parameters=["Отказ", "РежимПроведения"],
        context="НаСервере",
        body="Если Отказ Тогда\n\tВозврат;\nКонецЕсли;",
    )

    module = workspace.read_text("Extensions/HarnessExt/Documents/Заказ/Ext/ObjectModule.bsl")
    assert '&Перед("ОбработкаПроведения")' in module
    assert "&НаСервере" in module
    assert "Процедура Harness_ОбработкаПроведения(Отказ, РежимПроведения)" in module
    assert "Если Отказ Тогда" in module
    assert change.snapshot_id
