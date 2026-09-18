export type StepStatus = 'done' | 'running' | 'failed' | 'waiting';
export interface AgentStep { id: string; label: string; detail?: string; status: StepStatus }
export interface PatchPreview { id: string; file: string; language: 'bsl' | 'xml' | 'text'; original: string; modified: string; snapshotId?: string }
export interface ReviewFile { path: string; original: string; modified: string; created: boolean; deleted: boolean }
export interface Session {
  deployment?: string; backup?: string;
  id: string; task: string; summary: string; status: string; review_state: 'pending' | 'accepted' | 'rejected';
  checks_ok: boolean | null; ui_test_ok: boolean | null; files: ReviewFile[];
  steps: { tool: string; args: Record<string, unknown>; result: string }[];
}
export interface HarnessDoctor {
  llm_provider: string; llm_model: string; llm_credentials: boolean; onec_exe: string | null;
  exe_exists: boolean; onec_connection: boolean; staging_connection: boolean; com_configured: boolean;
  runtime_writes: boolean; test_client_connection: boolean; test_manager_connection: boolean;
  e2e_ui_testing: boolean; test_port: number; workspace: string; source_count: number;
  can_run: boolean; can_check: boolean; errors: string[];
}
export interface DesktopSettings { values: Record<string, string | null>; secrets: Record<string, boolean> }

export interface DiscoveredInfobase { name: string; connection: string; file_path?: string | null }
export interface DiscoveryResult { executables: string[]; infobases: DiscoveredInfobase[]; suggested_workspace: string }
