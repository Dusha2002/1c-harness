import { Channel, invoke } from '@tauri-apps/api/core';
export interface Progress { type: string; tool?: string; result?: string; iteration?: number }
export async function request<T>(op: string, args: Record<string, unknown> = {}, progress?: (event: Progress) => void): Promise<T> {
  const onEvent = new Channel<Progress>();
  onEvent.onmessage = event => progress?.(event);
  return invoke<T>('desktop_request', { request: { op, ...args }, onEvent });
}

export async function pickPath(kind: 'exe' | 'folder' | 'skill'): Promise<string | null> {
  return invoke<string | null>('pick_path', { kind });
}
