/**
 * Agents store — reactive agent list.
 *
 * Initialised by fetching GET /api/agents.
 * Updated in real-time from SSE `agent_status` events.
 *
 * Issue #37
 */

import type { Agent, AgentStatus } from '$lib/types/api.js';

let agents = $state<Agent[]>([]);
let loading = $state(false);
let error = $state<string | null>(null);

export const agentsStore = {
	get list() {
		return agents;
	},
	get loading() {
		return loading;
	},
	get error() {
		return error;
	},

	/** Fetch agents from the proxy route. */
	async refresh() {
		loading = true;
		error = null;
		try {
			const res = await fetch('/api/agents');
			const body = await res.json();
			if (res.ok && body.data) {
				agents = body.data;
			} else {
				error = body.errors?.[0] ?? 'Failed to fetch agents';
			}
		} catch (err) {
			error = err instanceof Error ? err.message : 'Network error';
		} finally {
			loading = false;
		}
	},

	/** Apply an SSE agent_status event. */
	handleSSE(data: { agent: string; status: AgentStatus; task_id: string | null }) {
		agents = agents.map((a) =>
			a.id === data.agent ? { ...a, status: data.status, current_task_id: data.task_id } : a
		);
	},

	/** Reset to empty state (used on disconnect). */
	reset() {
		agents = [];
		error = null;
	}
};
