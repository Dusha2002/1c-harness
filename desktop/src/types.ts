export type StepStatus = "done" | "running" | "failed" | "waiting";

export interface AgentStep {
  id: string;
  label: string;
  detail?: string;
  status: StepStatus;
}

export interface AgentResult {
  summary: string;
  snapshots: string[];
  checks_ok: boolean | null;
  ui_test_ok: boolean | null;
  steps: Array<{
    tool: string;
    args: Record<string, unknown>;
    result: string;
  }>;
}

export interface PatchPreview {
  id: string;
  file: string;
  language: "bsl" | "xml" | "text";
  original: string;
  modified: string;
  snapshotId?: string;
}

export interface HarnessDoctor {
  llm_provider: string;
  llm_model: string;
  llm_credentials: boolean;
  onec_exe: string | null;
  onec_connection: boolean;
  staging_connection: boolean;
  com_configured: boolean;
  runtime_writes: boolean;
  test_client_connection: boolean;
  test_manager_connection: boolean;
  e2e_ui_testing: boolean;
  test_port: number;
  workspace: string;
}
