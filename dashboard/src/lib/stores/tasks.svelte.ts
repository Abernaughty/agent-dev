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
 * Issue #92: handleProgress() now pushes timeline events + budget updates;
 *            handleComplete() sets completed_at
 * Issue #106: create() accepts workspace, pin, publish_pr
 * Issue #107: handleProgress() attaches sandbox output fields for sandbox_validated events
 * Issue #108: fetchDetail() for on-demand TaskDetail loading;
 *            handleComplete() stores completion_detail;
 *            detail cache map for TaskDetail responses
 */

import type { TaskSummary, TaskDetail, TaskStatus, CreateTaskResponse, ToolCallEvent, TimelineEvent } from '$lib/types/api.js';

let tasks = $state<TaskSummary[]>([]);
let loading = $state(false);
let error = $state<string | null>(null);

/**
 * Issue #108: Cache of TaskDetail responses keyed by task_id.
 * Populated on-demand when a user clicks a task in the sidebar.
 */
let detailCache = $state<Map<string, TaskDetail>>(new Map());
let detailLoading = $state<string | null>(null);
let detailError = $state<string | null>(null);

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

/**
 * Map SSE task_progress event names to timeline event types.
 * Used by handleProgress() to create proper timeline entries.
 * Issue #92: Enables agent activity logs + Architect activity.
 */
const EVENT_TO_TIMELINE_TYPE: Record<string, string> = {
	blueprint_created: 'plan',
	code_generated: 'code',
	code_applied: 'exec',
	sandbox_validated: 'exec',
	qa_complete: 'exec',
	task_started: 'plan',
	task_retried: 'retry'
};

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

	// -- Issue #108: TaskDetail access --

	/** Get cached TaskDetail for a given task_id, or null if not fetched yet. */
	getDetail(taskId: string): TaskDetail | null {
		return detailCache.get(taskId) ?? null;
	},

	/** Whether a detail fetch is currently in progress. */
	get detailLoading() {
		return detailLoading;
	},

	/** Error from the most recent detail fetch, if any. */
	get detailError() {
		return detailError;
	},

	/**
	 * Fetch full TaskDetail from GET /api/tasks/{id}.
	 * Caches the result. Subsequent calls for the same ID return the cache
	 * unless force=true is passed.
	 *
	 * Issue #108: On-demand detail loading for click-to-expand.
	 */
	async fetchDetail(taskId: string, force = false): Promise<TaskDetail | null> {
		if (!force && detailCache.has(taskId)) {
			return detailCache.get(taskId)!;
		}

		detailLoading = taskId;
		detailError = null;
		try {
			const res = await fetch(`/api/tasks/${taskId}`);
			const body = await res.json();
			if (res.ok && body.data) {
				const detail = body.data as TaskDetail;
				const next = new Map(detailCache);
				next.set(taskId, detail);
				detailCache = next;
				return detail;
			}
			detailError = body.errors?.[0] ?? 'Failed to fetch task detail';
			return null;
		} catch (err) {
			detailError = err instanceof Error ? err.message : 'Network error';
			return null;
		} finally {
			detailLoading = null;
		}
	},

	/**
	 * Invalidate cached detail for a task (e.g. after a retry or new events).
	 * The next getDetail() call will re-fetch from the API.
	 */
	invalidateDetail(taskId: string) {
		if (detailCache.has(taskId)) {
			const next = new Map(detailCache);
			next.delete(taskId);
			detailCache = next;
		}
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

	/**
	 * Create a new task with workspace context.
	 *
	 * Issue #106: Now requires workspace (from workspacesStore.selected).
	 * Optional pin for protected workspaces, publish_pr to control PR creation.
	 * Returns the task_id on success, null on failure.
	 */
	async create(
		description: string,
		workspace: string,
		options?: { pin?: string; publish_pr?: boolean | null }
	): Promise<string | null> {
		try {
			const payload: Record<string, unknown> = { description, workspace };
			if (options?.pin) payload.pin = options.pin;
			if (options?.publish_pr !== undefined) payload.publish_pr = options.publish_pr;

			const res = await fetch('/api/tasks', {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify(payload)
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
						timeline: [],
						workspace
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
				// Invalidate cached detail so it's re-fetched with new state
				this.invalidateDetail(taskId);
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

	/**
	 * Apply an SSE task_progress event.
	 *
	 * Issue #92: Now pushes timeline events AND updates budget.
	 * Previously this was a no-op that just re-spread the array.
	 */
	handleProgress(data: {
		task_id: string;
		event: string;
		agent: string | null;
		detail: string;
		tokens_used?: number;
		retries_used?: number;
		cost_used?: number;
		token_budget?: number;
		max_retries?: number;
		cost_budget?: number;
	}) {
		const idx = tasks.findIndex((t) => t.id === data.task_id);
		if (idx < 0) return;

		const task = tasks[idx];

		// Build a timeline event from the progress data
		const timelineType = EVENT_TO_TIMELINE_TYPE[data.event] ?? 'exec';
		const agentId = data.agent === 'developer' ? 'dev'
			: data.agent === 'qa' ? 'qa'
			: data.agent === 'architect' ? 'arch'
			: data.agent ?? 'system';

		// Only add timeline entry if we have meaningful detail
		// Skip generic events like task_queued that don't add info
		const skipEvents = new Set(['task_queued', 'task_started']);
		// Issue #107: Extract sandbox output fields for sandbox_validated events
		const sandboxExtra: Record<string, unknown> = {};
		if (data.event === 'sandbox_validated') {
			if (typeof (data as Record<string, unknown>).output_summary === 'string') {
				sandboxExtra.output_summary = (data as Record<string, unknown>).output_summary;
			}
			if (Array.isArray((data as Record<string, unknown>).errors)) {
				sandboxExtra.errors = (data as Record<string, unknown>).errors;
			}
			if (typeof (data as Record<string, unknown>).exit_code === 'number') {
				sandboxExtra.exit_code = (data as Record<string, unknown>).exit_code;
			}
		}

		const newTimeline = skipEvents.has(data.event)
			? task.timeline
			: [...task.timeline, {
				time: nowTime(),
				agent: agentId,
				action: data.detail,
				type: timelineType,
				sandbox: 'locked',
				...sandboxExtra
			}];

		// Update budget if included in the SSE payload (Issue #92 fix 4)
		const newBudget = { ...task.budget };
		if (typeof data.tokens_used === 'number') newBudget.tokens_used = data.tokens_used;
		if (typeof data.retries_used === 'number') newBudget.retries_used = data.retries_used;
		if (typeof data.cost_used === 'number') newBudget.cost_used = data.cost_used;
		if (typeof data.token_budget === 'number') newBudget.token_budget = data.token_budget;
		if (typeof data.max_retries === 'number') newBudget.max_retries = data.max_retries;
		if (typeof data.cost_budget === 'number') newBudget.cost_budget = data.cost_budget;

		// Issue #108: Invalidate detail cache when new progress events arrive
		this.invalidateDetail(data.task_id);

		tasks = tasks.map((t, i) =>
			i === idx ? { ...t, timeline: newTimeline, budget: newBudget } : t
		);
	},

	/**
	 * Apply an SSE task_complete event.
	 * Issue #92: Also sets completed_at for debrief status detection.
	 * Issue #108: Stores completion detail for quick display before
	 *            full TaskDetail is fetched.
	 */
	handleComplete(data: { task_id: string; status: string; detail: string }) {
		// Issue #108: Invalidate detail cache so next click re-fetches
		this.invalidateDetail(data.task_id);

		tasks = tasks.map((t) =>
			t.id === data.task_id
				? {
						...t,
						status: data.status as TaskStatus,
						completed_at: new Date().toISOString(),
						completion_detail: data.detail
					}
				: t
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
		detailCache = new Map();
		detailLoading = null;
		detailError = null;
		pendingToolCalls.clear();
	}
};
