/**
 * SSE client — connects to /api/stream and dispatches events to stores.
 *
 * Features:
 * - Auto-reconnect with exponential backoff (1s → 2s → 4s → max 30s)
 * - Typed event dispatch to appropriate stores
 * - Connection status exposed via the connection store
 *
 * Usage (in a Svelte component or layout):
 *   import { sseClient } from '$lib/sse.js';
 *
 *   $effect(() => {
 *     sseClient.connect();
 *     return () => sseClient.disconnect();
 *   });
 *
 * Issue #37
 * Issue #85: Added tool_call event handling
 */

import { connection } from '$lib/stores/connection.svelte.js';
import { agentsStore } from '$lib/stores/agents.svelte.js';
import { tasksStore } from '$lib/stores/tasks.svelte.js';
import { memoryStore } from '$lib/stores/memory.svelte.js';
import type { SSEEventType, ToolCallEvent } from '$lib/types/api.js';

/** Backoff config */
const INITIAL_DELAY_MS = 1000;
const MAX_DELAY_MS = 30_000;
const BACKOFF_MULTIPLIER = 2;

let eventSource: EventSource | null = null;
let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
let attempt = 0;
let intentionalClose = false;

function getBackoffDelay(): number {
	const delay = INITIAL_DELAY_MS * Math.pow(BACKOFF_MULTIPLIER, attempt);
	return Math.min(delay, MAX_DELAY_MS);
}

/**
 * Type guard for ToolCallEvent payloads (CodeRabbit fix #1).
 * Validates all required fields are present with correct types
 * before passing to the store handler.
 */
function isToolCallEvent(payload: Record<string, unknown>): payload is ToolCallEvent {
	return (
		typeof payload.task_id === 'string' &&
		typeof payload.agent === 'string' &&
		typeof payload.tool === 'string' &&
		typeof payload.success === 'boolean' &&
		typeof payload.result_preview === 'string'
	);
}

/**
 * Route an SSE event to the appropriate store handler.
 */
function dispatch(eventType: SSEEventType, payload: Record<string, unknown>) {
	switch (eventType) {
		case 'agent_status':
			agentsStore.handleSSE(
				payload as { agent: string; status: Parameters<typeof agentsStore.handleSSE>[0]['status']; task_id: string | null }
			);
			break;

		case 'task_progress':
			tasksStore.handleProgress(
				payload as { task_id: string; event: string; agent: string | null; detail: string }
			);
			break;

		case 'task_complete':
			tasksStore.handleComplete(
				payload as { task_id: string; status: string; detail: string }
			);
			break;

		case 'memory_added':
			memoryStore.handleSSE(
				payload as { id: string; tier: string; agent: string; content: string; status: string }
			);
			break;

		case 'tool_call':
			if (isToolCallEvent(payload)) {
				tasksStore.handleToolCall(payload);
			}
			break;

		case 'log_line':
			// Log lines are consumed by the BottomPanel directly.
			// For now, dispatch a custom DOM event that components can listen to.
			if (typeof window !== 'undefined') {
				window.dispatchEvent(
					new CustomEvent('sse:log_line', { detail: payload })
				);
			}
			break;
	}
}

function handleMessage(eventType: string, rawData: string) {
	try {
		const parsed = JSON.parse(rawData);
		const data = parsed.data ?? parsed;
		dispatch(eventType as SSEEventType, data);
	} catch {
		// Malformed JSON — skip silently
	}
}

function scheduleReconnect() {
	if (intentionalClose) return;

	attempt++;
	const delay = getBackoffDelay();
	connection.setReconnecting(attempt);

	reconnectTimer = setTimeout(() => {
		reconnectTimer = null;
		connect();
	}, delay);
}

function connect() {
	// Clean up any existing connection
	if (eventSource) {
		eventSource.close();
		eventSource = null;
	}

	intentionalClose = false;

	try {
		eventSource = new EventSource('/api/stream');

		eventSource.onopen = () => {
			attempt = 0;
			connection.setConnected();

			// Refresh all stores on reconnect to catch up
			agentsStore.refresh();
			tasksStore.refresh();
			memoryStore.refresh();
		};

		eventSource.onerror = () => {
			if (eventSource) {
				eventSource.close();
				eventSource = null;
			}
			if (!intentionalClose) {
				scheduleReconnect();
			}
		};

		// Listen for each known event type
		const eventTypes: SSEEventType[] = [
			'agent_status',
			'task_progress',
			'task_complete',
			'memory_added',
			'log_line',
			'tool_call'
		];

		for (const type of eventTypes) {
			eventSource.addEventListener(type, (event: MessageEvent) => {
				handleMessage(type, event.data);
			});
		}
	} catch {
		scheduleReconnect();
	}
}

function disconnect() {
	intentionalClose = true;

	if (reconnectTimer) {
		clearTimeout(reconnectTimer);
		reconnectTimer = null;
	}

	if (eventSource) {
		eventSource.close();
		eventSource = null;
	}

	attempt = 0;
	connection.setDisconnected();
}

export const sseClient = {
	connect,
	disconnect,

	/** Whether the SSE connection is currently open. */
	get isConnected() {
		return eventSource?.readyState === EventSource.OPEN;
	}
};
