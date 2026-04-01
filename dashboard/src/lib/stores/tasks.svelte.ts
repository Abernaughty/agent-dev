/**
 * Tasks store — reactive task list.
 *
 * Initialised by fetching GET /api/tasks.
 * Updated in real-time from SSE `task_progress`, `task_complete`,
 * and `tool_call` events.
 * Supports mutations: create, cancel, retry.
 *
 * Issue #37
 * Issue #85: Added handleToolCall() for TOOL_CALL SSE events
 */

import type { TaskSummary, TaskStatus, CreateTaskResponse, ToolCallEvent, TimelineEvent } from '$lib/types/api.js';

let tasks = $state<TaskSummary[]>([]);
let loading = $state(false);
let error = $state<string | null>(null);

/**
 * Buffer for tool call events that arrive before the task exists in memory
 * or while a refresh() is in flight. Keyed by task_id.
 * (CodeRabbit fix #2 — prevent reconnect hydration from erasing in-flight tool calls)
 */
const pendingToolCalls = new Map<string, TimelineEvent[]>();

/** Format current time as HH:MM for timeline entries. */
function nowTime(): string {
	const d = new Date();
	return `${d.getHours().toString().padStart(2, '0')}:${d.getMinutes().toString().padStart(2, '0')}`;
}

/** Convert a ToolCallEvent into a TimelineEvent for the task timeline. */
function toolCallToTimeline(data: ToolCallEvent): TimelineEvent {
	const statusIcon = data.success ? '\u2713' : '\u2717';
	const preview = data.result_preview
		? `: ${data.result_preview.slice(0, 80)}${data.result_preview.length > 80 ? '...' : ''}`
		: '';
	return {
		time: nowTime(),
		agent: data.agent === 'developer' ? 'dev' : data.agent === 'qa' ? 'qa' : data.agent,
		action: `${statusIcon} ${data.tool}${preview}`,
		type: 'tool_call',
		sandbox: 'locked'
	};
}

/**
 * Replay any buffered tool call timeline events into a task's timeline.
 * Returns the merged timeline, or the original if no buffered events exist.
 */
function replayBuffered(task: TaskSummary): TimelineEvent[] {
	const buffered = pendingToolCalls.get(task.id);
	if (!buffered || buffered.length === 0) return task.timeline;
	pendingToolCalls.delete(task.id);
	return [...task.timeline, ...buffered];
}

export const tasksStore = {
	get list() {
		return tasks;
	},
	get loading() {
		return loading;
	},
	get error() {
		return error;
	},
	get activeTasks() {
		const active: TaskStatus[] = ['queued', 'planning', 'building', 'reviewing'];
		return tasks.filter((t) => active.includes(t.status));
	},

	/**
	 * Fetch tasks from the proxy route.
	 * After fetching, replays any buffered tool call events that arrived
	 * while the fetch was in flight (CodeRabbit fix #2).
	 */
	async refresh() {
		loading = true;
		error = null;
		try {
			const res = await fetch('/api/tasks');
			const body = await res.json();
			if (res.ok && body.data) {
				const fetched = body.data as TaskSummary[];
				// Merge buffered tool call events into fetched tasks
				tasks = fetched.map((t) => ({
					...t,
					timeline: replayBuffered(t)
				}));
			} else {
				error = body.errors?.[0] ?? 'Failed to fetch tasks';
			}
		} catch (err) {
			error = err instanceof Error ? err.message : 'Network error';
		} finally {
			loading = false;
		}
	},

	/** Create a new task. Returns the task_id on success. */
	async create(description: string): Promise<string | null> {
		try {
			const res = await fetch('/api/tasks', {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ description })
			});
			const body = await res.json();
			if (res.ok && body.data) {
				const created = body.data as CreateTaskResponse;
				tasks = [
					...tasks,
					{
						id: created.task_id,
						description,
						status: created.status,
						created_at: new Date().toISOString(),
						completed_at: null,
						budget: {
							tokens_used: 0,
							token_budget: 50000,
							retries_used: 0,
							max_retries: 3,
							cost_used: 0,
							cost_budget: 1.0
						},
						timeline: []
					}
				];
				return created.task_id;
			}
			error = body.errors?.[0] ?? 'Failed to create task';
			return null;
		} catch (err) {
			error = err instanceof Error ? err.message : 'Network error';
			return null;
		}
	},

	/** Cancel a running task. */
	async cancel(taskId: string): Promise<boolean> {
		try {
			const res = await fetch(`/api/tasks/${taskId}/cancel`, { method: 'POST' });
			if (res.ok) {
				tasks = tasks.map((t) => (t.id === taskId ? { ...t, status: 'cancelled' as const } : t));
				return true;
			}
			const body = await res.json();
			error = body.errors?.[0] ?? 'Failed to cancel task';
			return false;
		} catch (err) {
			error = err instanceof Error ? err.message : 'Network error';
			return false;
		}
	},

	/** Retry a failed task. */
	async retry(taskId: string): Promise<boolean> {
		try {
			const res = await fetch(`/api/tasks/${taskId}/retry`, { method: 'POST' });
			if (res.ok) {
				tasks = tasks.map((t) => (t.id === taskId ? { ...t, status: 'queued' as const } : t));
				return true;
			}
			const body = await res.json();
			error = body.errors?.[0] ?? 'Failed to retry task';
			return false;
		} catch (err) {
			error = err instanceof Error ? err.message : 'Network error';
			return false;
		}
	},

	/** Apply an SSE task_progress event. */
	handleProgress(data: {
		task_id: string;
		event: string;
		agent: string | null;
		detail: string;
	}) {
		const idx = tasks.findIndex((t) => t.id === data.task_id);
		if (idx >= 0) {
			tasks = [...tasks];
		}
	},

	/** Apply an SSE task_complete event. */
	handleComplete(data: { task_id: string; status: string; detail: string }) {
		tasks = tasks.map((t) =>
			t.id === data.task_id ? { ...t, status: data.status as TaskStatus } : t
		);
	},

	/**
	 * Apply an SSE tool_call event (Issue #85).
	 *
	 * Appends tool calls as timeline events on the matching task.
	 * If the task isn't in memory yet (e.g. during reconnect refresh),
	 * buffers the event for replay once refresh() completes (CodeRabbit fix #2).
	 */
	handleToolCall(data: ToolCallEvent) {
		const timelineEvent = toolCallToTimeline(data);

		const idx = tasks.findIndex((t) => t.id === data.task_id);
		if (idx < 0) {
			// Task not in memory — buffer for replay after refresh()
			const existing = pendingToolCalls.get(data.task_id) ?? [];
			existing.push(timelineEvent);
			pendingToolCalls.set(data.task_id, existing);
			return;
		}

		const task = tasks[idx];
		const newTimeline = [...task.timeline, timelineEvent];

		tasks = tasks.map((t, i) =>
			i === idx ? { ...t, timeline: newTimeline } : t
		);
	},

	/** Reset to empty state. */
	reset() {
		tasks = [];
		error = null;
		pendingToolCalls.clear();
	}
};
