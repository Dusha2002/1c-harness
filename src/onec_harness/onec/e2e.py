from __future__ import annotations

import socket
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from onec_harness.onec.designer import CommandResult, Designer
from onec_harness.onec.testing import ScenarioCompiler, TestClientError, TestClientLauncher, TestProcess
from onec_harness.settings import Settings
from onec_harness.workspace import Workspace


EPF_NAMESPACES = (
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

FORM_NAMESPACES = (
    'xmlns="http://v8.1c.ru/8.3/xcf/logform" '
    'xmlns:app="http://v8.1c.ru/8.2/managed-application/core" '
    'xmlns:cfg="http://v8.1c.ru/8.1/data/enterprise/current-config" '
    'xmlns:dcscor="http://v8.1c.ru/8.1/data-composition-system/core" '
    'xmlns:dcsset="http://v8.1c.ru/8.1/data-composition-system/settings" '
    'xmlns:ent="http://v8.1c.ru/8.1/data/enterprise" '
    'xmlns:lf="http://v8.1c.ru/8.2/managed-application/logform" '
    'xmlns:style="http://v8.1c.ru/8.1/data/ui/style" '
    'xmlns:sys="http://v8.1c.ru/8.1/data/ui/fonts/system" '
    'xmlns:v8="http://v8.1c.ru/8.1/data/core" '
    'xmlns:v8ui="http://v8.1c.ru/8.1/data/ui" '
    'xmlns:web="http://v8.1c.ru/8.1/data/ui/colors/web" '
    'xmlns:win="http://v8.1c.ru/8.1/data/ui/colors/windows" '
    'xmlns:xr="http://v8.1c.ru/8.3/xcf/readable" '
    'xmlns:xs="http://www.w3.org/2001/XMLSchema" '
    'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"'
)


@dataclass(slots=True, frozen=True)
class ScenarioArtifacts:
    run_id: str
    root: Path
    source_xml: Path
    epf_path: Path
    result_path: Path
    manager_log: Path
    name: str


@dataclass(slots=True)
class ScenarioRunResult:
    run_id: str
    success: bool | None
    status: str
    details: str
    build: CommandResult
    client: TestProcess | None
    manager: TestProcess | None
    artifacts: ScenarioArtifacts
    duration_seconds: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "success": self.success,
            "status": self.status,
            "details": self.details,
            "duration_seconds": round(self.duration_seconds, 3),
            "epf": str(self.artifacts.epf_path),
            "result": str(self.artifacts.result_path),
            "manager_log": str(self.artifacts.manager_log),
            "client_pid": self.client.pid if self.client else None,
            "manager_pid": self.manager.pid if self.manager else None,
            "build_ok": self.build.ok if self.build.executed else None,
        }


class ExternalScenarioPackage:
    """Generate a tiny external data processor that executes a Test Manager scenario on form open."""

    def __init__(self, workspace: Workspace, compiler: ScenarioCompiler, *, format_version: str = "2.17") -> None:
        self.workspace = workspace
        self.compiler = compiler
        self.format_version = format_version

    @staticmethod
    def _bsl_string(value: str) -> str:
        return value.replace('"', '""')

    def _root_xml(self, name: str, ids: list[str]) -> str:
        return f'''<?xml version="1.0" encoding="UTF-8"?>
<MetaDataObject {EPF_NAMESPACES} version="{self.format_version}">
\t<ExternalDataProcessor uuid="{ids[0]}">
\t\t<InternalInfo>
\t\t\t<xr:ContainedObject>
\t\t\t\t<xr:ClassId>c3831ec8-d8d5-4f93-8a22-f9bfae07327f</xr:ClassId>
\t\t\t\t<xr:ObjectId>{ids[1]}</xr:ObjectId>
\t\t\t</xr:ContainedObject>
\t\t\t<xr:GeneratedType name="ExternalDataProcessorObject.{name}" category="Object">
\t\t\t\t<xr:TypeId>{ids[2]}</xr:TypeId>
\t\t\t\t<xr:ValueId>{ids[3]}</xr:ValueId>
\t\t\t</xr:GeneratedType>
\t\t</InternalInfo>
\t\t<Properties>
\t\t\t<Name>{escape(name)}</Name>
\t\t\t<Synonym><v8:item><v8:lang>ru</v8:lang><v8:content>{escape(name)}</v8:content></v8:item></Synonym>
\t\t\t<Comment/>
\t\t\t<DefaultForm>ExternalDataProcessor.{name}.Form.Форма</DefaultForm>
\t\t\t<AuxiliaryForm/>
\t\t</Properties>
\t\t<ChildObjects><Form>Форма</Form></ChildObjects>
\t</ExternalDataProcessor>
</MetaDataObject>
'''

    def _form_meta_xml(self, form_uuid: str) -> str:
        return f'''<?xml version="1.0" encoding="UTF-8"?>
<MetaDataObject {EPF_NAMESPACES} version="{self.format_version}">
\t<Form uuid="{form_uuid}">
\t\t<Properties>
\t\t\t<Name>Форма</Name>
\t\t\t<Synonym><v8:item><v8:lang>ru</v8:lang><v8:content>Harness runner</v8:content></v8:item></Synonym>
\t\t\t<Comment/>
\t\t\t<FormType>Managed</FormType>
\t\t\t<IncludeHelpInContents>false</IncludeHelpInContents>
\t\t\t<UsePurposes><v8:Value xsi:type="app:ApplicationUsePurpose">PlatformApplication</v8:Value></UsePurposes>
\t\t\t<ExtendedPresentation/>
\t\t</Properties>
\t</Form>
</MetaDataObject>
'''

    def _form_xml(self, name: str) -> str:
        return f'''<?xml version="1.0" encoding="UTF-8"?>
<Form {FORM_NAMESPACES} version="{self.format_version}">
\t<AutoCommandBar name="ФормаКоманднаяПанель" id="-1"><Autofill>true</Autofill></AutoCommandBar>
\t<Events><Event name="OnOpen">ПриОткрытии</Event></Events>
\t<ChildItems/>
\t<Attributes>
\t\t<Attribute name="Объект" id="1">
\t\t\t<Type><v8:Type>cfg:ExternalDataProcessorObject.{name}</v8:Type></Type>
\t\t\t<MainAttribute>true</MainAttribute>
\t\t</Attribute>
\t</Attributes>
</Form>
'''

    def _form_module(self, actions: list[dict[str, Any]], result_path: Path) -> str:
        scenario = self.compiler.compile(actions, procedure_name="RunHarnessScenario").rstrip()
        target = self._bsl_string(str(result_path))
        wrapper = f'''

&НаКлиенте
Процедура ПриОткрытии(Отказ)
    Отказ = Ложь;
    СтатусHarness = "OK";
    ДеталиHarness = "";
    Попытка
        RunHarnessScenario();
    Исключение
        СтатусHarness = "FAILED";
        ДеталиHarness = ОписаниеОшибки();
    КонецПопытки;
    ЗаписатьРезультатHarness(СтатусHarness, ДеталиHarness);
    ЗавершитьРаботуСистемы();
КонецПроцедуры

&НаКлиенте
Процедура ЗаписатьРезультатHarness(Статус, Детали)
    ЗаписьHarness = Новый ЗаписьТекста("{target}", КодировкаТекста.UTF8);
    ЗаписьHarness.ЗаписатьСтроку(Статус);
    Если НЕ ПустаяСтрока(Детали) Тогда
        ЗаписьHarness.Записать(Детали);
    КонецЕсли;
    ЗаписьHarness.Закрыть();
КонецПроцедуры
'''
        return scenario + wrapper

    def build_sources(self, actions: list[dict[str, Any]]) -> ScenarioArtifacts:
        run_id = uuid.uuid4().hex[:12]
        name = f"HarnessScenario_{run_id}"
        root = self.workspace.resolve(Path(".onec-harness") / "e2e" / run_id)
        src = root / "src"
        result_path = root / "result.txt"
        epf_path = root / f"{name}.epf"
        manager_log = root / "manager.log"
        form_ext = src / name / "Forms" / "Форма" / "Ext"
        object_ext = src / name / "Ext"
        form_ext.mkdir(parents=True, exist_ok=True)
        object_ext.mkdir(parents=True, exist_ok=True)

        ids = [str(uuid.uuid4()) for _ in range(5)]
        source_xml = src / f"{name}.xml"
        source_xml.write_text(self._root_xml(name, ids[:4]), encoding="utf-8-sig")
        (src / name / "Forms" / "Форма.xml").write_text(self._form_meta_xml(ids[4]), encoding="utf-8-sig")
        (form_ext / "Form.xml").write_text(self._form_xml(name), encoding="utf-8-sig")
        (form_ext / "Module.bsl").write_text(self._form_module(actions, result_path), encoding="utf-8-sig")
        (object_ext / "ObjectModule.bsl").write_text("", encoding="utf-8-sig")

        return ScenarioArtifacts(
            run_id=run_id,
            root=root,
            source_xml=source_xml,
            epf_path=epf_path,
            result_path=result_path,
            manager_log=manager_log,
            name=name,
        )


class TestManagerRunner:
    """Build and execute a generated Test Manager EPF against a disposable test client."""

    def __init__(self, settings: Settings, workspace: Workspace) -> None:
        self.settings = settings
        self.workspace = workspace
        if not settings.onec_staging_ib_connection.strip():
            raise TestClientError("ONEC_STAGING_IB_CONNECTION is required to build E2E runner EPF")
        if not settings.onec_test_manager_connection.strip():
            raise TestClientError("ONEC_TEST_MANAGER_CONNECTION is required for E2E UI tests")
        self.compiler = ScenarioCompiler(settings.onec_test_host, settings.onec_test_port)
        self.launcher = TestClientLauncher(settings)
        self.designer = Designer(
            settings,
            connection_override=settings.onec_staging_ib_connection,
            auth_kind="staging",
        )
        self.package = ExternalScenarioPackage(workspace, self.compiler)

    def _wait_for_port(self, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                with socket.create_connection((self.settings.onec_test_host, self.settings.onec_test_port), timeout=0.5):
                    return True
            except OSError:
                time.sleep(0.25)
        return False

    @staticmethod
    def _stop(process: TestProcess | None) -> None:
        if process is None or process.process is None or process.process.poll() is not None:
            return
        process.process.terminate()
        try:
            process.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.process.kill()

    @staticmethod
    def _parse_result(path: Path) -> tuple[bool, str, str]:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        first, _, rest = text.partition("\n")
        status = first.strip() or "UNKNOWN"
        return status == "OK", status, rest.strip()

    def run(self, actions: list[dict[str, Any]], *, execute: bool = False) -> ScenarioRunResult:
        started = time.monotonic()
        artifacts = self.package.build_sources(actions)
        build = self.designer.build_external_processor(artifacts.source_xml, artifacts.epf_path, execute=execute)
        if not execute:
            return ScenarioRunResult(
                run_id=artifacts.run_id,
                success=None,
                status="DRY_RUN",
                details="Scenario sources prepared; 1C processes were not started",
                build=build,
                client=None,
                manager=None,
                artifacts=artifacts,
                duration_seconds=time.monotonic() - started,
            )
        if not build.ok:
            return ScenarioRunResult(
                run_id=artifacts.run_id,
                success=False,
                status="BUILD_FAILED",
                details=build.combined_output(),
                build=build,
                client=None,
                manager=None,
                artifacts=artifacts,
                duration_seconds=time.monotonic() - started,
            )

        client: TestProcess | None = None
        manager: TestProcess | None = None
        try:
            client = self.launcher.launch_client(execute=True)
            if not self._wait_for_port(self.settings.onec_test_startup_timeout_seconds):
                return ScenarioRunResult(
                    run_id=artifacts.run_id,
                    success=False,
                    status="CLIENT_START_TIMEOUT",
                    details=f"Test Client did not open port {self.settings.onec_test_port}",
                    build=build,
                    client=client,
                    manager=None,
                    artifacts=artifacts,
                    duration_seconds=time.monotonic() - started,
                )
            manager = self.launcher.launch_manager(
                execute=True,
                external_processor=artifacts.epf_path,
                output=artifacts.manager_log,
            )
            deadline = time.monotonic() + self.settings.onec_test_timeout_seconds
            while time.monotonic() < deadline:
                if artifacts.result_path.exists():
                    success, status, details = self._parse_result(artifacts.result_path)
                    return ScenarioRunResult(
                        run_id=artifacts.run_id,
                        success=success,
                        status=status,
                        details=details,
                        build=build,
                        client=client,
                        manager=manager,
                        artifacts=artifacts,
                        duration_seconds=time.monotonic() - started,
                    )
                if manager.process is not None and manager.process.poll() is not None:
                    break
                time.sleep(0.25)
            details = "Test Manager finished without result" if manager.process and manager.process.poll() is not None else "Scenario timed out"
            if artifacts.manager_log.exists():
                log = artifacts.manager_log.read_text(encoding="utf-8-sig", errors="replace").strip()
                if log:
                    details = f"{details}\n{log}"
            return ScenarioRunResult(
                run_id=artifacts.run_id,
                success=False,
                status="TIMEOUT_OR_NO_RESULT",
                details=details,
                build=build,
                client=client,
                manager=manager,
                artifacts=artifacts,
                duration_seconds=time.monotonic() - started,
            )
        finally:
            self._stop(manager)
            self._stop(client)
