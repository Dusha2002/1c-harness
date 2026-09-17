from pathlib import Path

from onec_harness.metadata import ConfigurationIndex
from onec_harness.semantic_forms import FormMetadataEditor
from onec_harness.workspace import Workspace


CONFIG = '''<?xml version="1.0" encoding="UTF-8"?>
<MetaDataObject xmlns="http://v8.1c.ru/8.3/MDClasses" version="2.17">
  <Configuration uuid="00000000-0000-0000-0000-000000000001">
    <Properties><Name>HarnessTest</Name></Properties>
    <ChildObjects/>
  </Configuration>
</MetaDataObject>
'''


def test_index_includes_managed_form_and_module(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path)
    workspace.write_text("Configuration.xml", CONFIG)
    editor = FormMetadataEditor(workspace)
    editor.create_catalog("Товары")
    editor.create_managed_form("catalog", "Товары", "ФормаЭлемента", purpose="Object")

    index = ConfigurationIndex.build(tmp_path)
    form = next(item for item in index.objects if item.kind == "form")

    assert form.name == "catalog.Товары.ФормаЭлемента"
    assert form.definition_path == "Catalogs/Товары/Forms/ФормаЭлемента.xml"
    assert form.modules == ("Catalogs/Товары/Forms/ФормаЭлемента/Ext/Form/Module.bsl",)
