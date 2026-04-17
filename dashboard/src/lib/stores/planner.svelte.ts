/**
 * Planner store — reactive Planner session state.
 *
 * Manages the conversational planner flow:
 * 1. Start session (POST /api/planner) with workspace
 * 2. Exchange messages (POST /api/planner/{id}/message)
 * 3. Submit to Architect (POST /api/planner/{id}/submit)
 *
 * Updated in real-time from SSE `planner_message` events.
 *
 * Issue #106 Phase B: ChatView planner UI
 * Chat rework: warnings no longer injected as event messages,
 * taskId field added for cross-blade navigation.
 */

import type {
	PlannerSessionResponse,
	PlannerSubmitResponse,
	PlannerChecklist,
	PlannerTaskSpec,
	PlannerMessageEvent
} from '$lib/types/api.js';

// -- Chat message type for display --

export interface PlannerChatMessage {
	role: 'user' | 'planner' | 'system' | 'event';
	text: string;
	time: string;
	/** Optional task ID for cross-blade navigation (set on submission messages). */
	taskId?: string;
}

// -- Planner session phases --

export type PlannerPhase =
	| 'idle'        // No active session
	| 'starting'    // POST /api/planner in flight
	| 'chatting'    // Active session, exchanging messages
	| 'sending'     // POST /api/planner/{id}/message in flight
	| 'submitting'  // POST /api/planner/{id}/submit in flight
	| 'submitted';  // Session submitted, task created

// -- State --

let sessionId = $state<string | null>(null);
let phase = $state<PlannerPhase>('idle');
let messages = $state<PlannerChatMessage[]>([]);
let taskSpec = $state<PlannerTaskSpec | null>(null);
let checklist = $state<PlannerChecklist | null>(null);
let ready = $state(false);
let warnings = $state<string[]>([]);
let error = $state<string | null>(null);
let submittedTaskId = $state<string | null>(null);

/**
 * Generation counter for stale-response detection.
 * Incremented on reset() and startSession(). Any async method
 * captures the current value before awaiting and bails if it
 * changed by the time the response arrives.
 * (CodeRabbit fix #3)
 */
let requestGeneration = $state(0);

/** Format current time as HH:MM. */
function nowTime(): string {
	return new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

/** Apply a PlannerSessionResponse to local state. */
function applySession(resp: PlannerSessionResponse) {
	sessionId = resp.session_id;
	taskSpec = resp.task_spec;
	checklist = resp.checklist;
	ready = resp.ready;
	warnings = resp.warnings ?? [];
}

export const plannerStore = {
	// -- Getters --

	get sessionId() { return sessionId; },
	get phase() { return phase; },
	get messages() { return messages; },
	get taskSpec() { return taskSpec; },
	get checklist() { return checklist; },
	get ready() { return ready; },
	get warnings() { return warnings; },
	get error() { return error; },
	get submittedTaskId() { return submittedTaskId; },

	/** Whether we have an active (non-submitted) session. */
	get hasActiveSession(): boolean {
		return sessionId !== null && phase !== 'idle' && phase !== 'submitted';
	},

	/** Whether the user can type a message. */
	get canSend(): boolean {
		return phase === 'chatting';
	},

	/** Whether the submit button should be available. */
	get canSubmit(): boolean {
		return phase === 'chatting' && ready;
	},

	// -- Actions --

	/**
	 * Start a new Planner session for a workspace.
	 *
	 * Calls POST /api/planner with the workspace path.
	 * On success, transitions to 'chatting' phase with the
	 * initial system message from the Planner.
	 */
	async startSession(
		workspace: string,
		options?: {
			pin?: string;
			workspace_type?: string;
			github_repo?: string | null;
		}
	): Promise<boolean> {
		const generation = ++requestGeneration;
		error = null;
		phase = 'starting';

		// Reset previous session
		messages = [];
		taskSpec = null;
		checklist = null;
		ready = false;
		warnings = [];
		submittedTaskId = null;

		try {
			// Issue #193: forward workspace_type + github_repo so the
			// Planner can resolve same-repo refs like "Issue #113" in
			// user messages using the correct default owner/repo.
			const payload: Record<string, string> = { workspace };
			if (options?.pin) payload.pin = options.pin;
			if (options?.workspace_type) payload.workspace_type = options.workspace_type;
			if (options?.github_repo) payload.github_repo = options.github_repo;

			const res = await fetch('/api/planner', {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify(payload)
			});
			const body = await res.json();

			// Stale-response guard (CodeRabbit fix #3)
			if (generation !== requestGeneration) return false;

			if (res.ok && body.data) {
				const resp = body.data as PlannerSessionResponse;
				applySession(resp);
				phase = 'chatting';

				// Add the initial system message
				if (resp.message) {
					messages = [{
						role: 'system',
						text: resp.message,
						time: nowTime()
					}];
				}

				return true;
			}

			error = body.errors?.[0] ?? 'Failed to start planner session';
			phase = 'idle';
			return false;
		} catch (err) {
			if (generation !== requestGeneration) return false;
			error = err instanceof Error ? err.message : 'Network error';
			phase = 'idle';
			return false;
		}
	},

	/**
	 * Send a user message to the Planner.
	 *
	 * Adds the user message to chat immediately, then calls
	 * POST /api/planner/{id}/message. On success, adds the
	 * Planner's response and updates checklist state.
	 *
	 * Warnings are stored in the store but NO LONGER injected
	 * as event messages — the pinned readiness header displays them.
	 */
	async sendMessage(text: string): Promise<boolean> {
		if (!sessionId || phase !== 'chatting') return false;

		const generation = requestGeneration;
		const currentSessionId = sessionId;
		error = null;

		// Add user message immediately
		messages = [...messages, {
			role: 'user',
			text,
			time: nowTime()
		}];

		phase = 'sending';

		try {
			const res = await fetch(`/api/planner/${currentSessionId}/message`, {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ message: text })
			});
			const body = await res.json();

			// Stale-response guard (CodeRabbit fix #3)
			if (generation !== requestGeneration || currentSessionId !== sessionId) return false;

			if (res.ok && body.data) {
				const resp = body.data as PlannerSessionResponse;
				applySession(resp);
				phase = 'chatting';

				// Add planner response
				if (resp.message) {
					messages = [...messages, {
						role: 'planner',
						text: resp.message,
						time: nowTime()
					}];
				}

				// Warnings are stored via applySession() but NOT
				// injected as chat event messages. The pinned
				// readiness header communicates checklist state.

				return true;
			}

			error = body.errors?.[0] ?? 'Failed to send message';
			phase = 'chatting'; // Allow retry
			messages = [...messages, {
				role: 'event',
				text: error ?? 'Message failed',
				time: nowTime()
			}];
			return false;
		} catch (err) {
			if (generation !== requestGeneration || currentSessionId !== sessionId) return false;
			error = err instanceof Error ? err.message : 'Network error';
			phase = 'chatting'; // Allow retry
			messages = [...messages, {
				role: 'event',
				text: error ?? 'Network error',
				time: nowTime()
			}];
			return false;
		}
	},

	/**
	 * Submit the current session to the Architect.
	 *
	 * Calls POST /api/planner/{id}/submit. On success, creates
	 * the task and transitions to 'submitted' phase.
	 *
	 * Issue #153: forwards create_pr and workspace type fields.
	 */
	async submit(options?: {
		create_pr?: boolean | null;
		workspace_type?: 'local' | 'github';
		github_repo?: string | null;
		github_branch?: string | null;
		github_feature_branch?: string | null;
	}): Promise<string | null> {
		if (!sessionId || !ready) return null;

		const generation = requestGeneration;
		const currentSessionId = sessionId;
		error = null;
		phase = 'submitting';

		try {
			const fetchOptions: RequestInit = { method: 'POST' };
			if (options) {
				fetchOptions.headers = { 'Content-Type': 'application/json' };
				fetchOptions.body = JSON.stringify(options);
			}
			const res = await fetch(`/api/planner/${currentSessionId}/submit`, fetchOptions);
			const body = await res.json();

			// Stale-response guard (CodeRabbit fix #3)
			if (generation !== requestGeneration || currentSessionId !== sessionId) return null;

			if (res.ok && body.data) {
				const resp = body.data as PlannerSubmitResponse;
				submittedTaskId = resp.task_id;
				phase = 'submitted';

				messages = [...messages, {
					role: 'system',
					text: `Task submitted. Routing to Architect for blueprint generation...`,
					time: nowTime(),
					taskId: resp.task_id
				}];

				return resp.task_id;
			}

			error = body.errors?.[0] ?? 'Failed to submit task';
			phase = 'chatting'; // Allow retry
			messages = [...messages, {
				role: 'event',
				text: error ?? 'Submit failed',
				time: nowTime()
			}];
			return null;
		} catch (err) {
			if (generation !== requestGeneration || currentSessionId !== sessionId) return null;
			error = err instanceof Error ? err.message : 'Network error';
			phase = 'chatting'; // Allow retry
			messages = [...messages, {
				role: 'event',
				text: error ?? 'Network error',
				time: nowTime()
			}];
			return null;
		}
	},

	/**
	 * Handle an SSE planner_message event.
	 *
	 * This fires when the Planner responds via SSE (in addition to
	 * the HTTP response). Useful for updating state if another tab
	 * is interacting with the same session.
	 */
	handleSSE(data: PlannerMessageEvent) {
		if (data.session_id !== sessionId) return;

		ready = data.ready;
		warnings = data.warnings ?? [];

		// Don't add a duplicate message — the HTTP response already
		// added it. SSE is for cross-tab sync only.
	},

	/**
	 * Reset to idle state. Discards the current session.
	 * Increments requestGeneration to invalidate any in-flight responses.
	 */
	reset() {
		requestGeneration++;
		sessionId = null;
		phase = 'idle';
		messages = [];
		taskSpec = null;
		checklist = null;
		ready = false;
		warnings = [];
		error = null;
		submittedTaskId = null;
	}
};
