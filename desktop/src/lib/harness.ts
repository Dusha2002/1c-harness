import { invoke } from "@tauri-apps/api/core";

import type { AgentResult, HarnessDoctor } from "../types";

interface HarnessOutput {
  status: number;
  stdout: string;
  stderr: string;
}

async function runHarness(args: string[]): Promise<HarnessOutput> {
  return invoke<HarnessOutput>("run_harness", { args });
}

function parseJson<T>(output: HarnessOutput): T {
  if (output.status !== 0) {
    throw new Error(output.stderr || output.stdout || `Harness exited with ${output.status}`);
  }
  try {
    return JSON.parse(output.stdout.trim()) as T;
  } catch (error) {
    throw new Error(`Harness returned invalid JSON: ${String(error)}`);
  }
}

export async function getDoctor(): Promise<HarnessDoctor> {
  return parseJson<HarnessDoctor>(await runHarness(["doctor", "--json"]));
}

export async function runAgent(task: string, options?: { write?: boolean; check?: boolean }): Promise<AgentResult> {
  const args = ["agent", task, "--json"];
  if (options?.write) args.push("--write");
  if (options?.check) args.push("--check");
  return parseJson<AgentResult>(await runHarness(args));
}

export async function restoreSnapshot(snapshotId: string): Promise<void> {
  const output = await runHarness(["restore-snapshot", snapshotId, "--yes"]);
  if (output.status !== 0) {
    throw new Error(output.stderr || output.stdout || `Harness exited with ${output.status}`);
  }
}
