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
		options?: { pin?: string }
	): Promise<boolean> {
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
			const payload: Record<string, string> = { workspace };
			if (options?.pin) payload.pin = options.pin;

			const res = await fetch('/api/planner', {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify(payload)
			});
			const body = await res.json();

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
	 */
	async sendMessage(text: string): Promise<boolean> {
		if (!sessionId || phase !== 'chatting') return false;

		error = null;

		// Add user message immediately
		messages = [...messages, {
			role: 'user',
			text,
			time: nowTime()
		}];

		phase = 'sending';

		try {
			const res = await fetch(`/api/planner/${sessionId}/message`, {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ message: text })
			});
			const body = await res.json();

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

				// Add warnings as event messages
				if (resp.warnings?.length) {
					for (const w of resp.warnings) {
						messages = [...messages, {
							role: 'event',
							text: w,
							time: nowTime()
						}];
					}
				}

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
	 */
	async submit(): Promise<string | null> {
		if (!sessionId || !ready) return null;

		error = null;
		phase = 'submitting';

		try {
			const res = await fetch(`/api/planner/${sessionId}/submit`, {
				method: 'POST'
			});
			const body = await res.json();

			if (res.ok && body.data) {
				const resp = body.data as PlannerSubmitResponse;
				submittedTaskId = resp.task_id;
				phase = 'submitted';

				messages = [...messages, {
					role: 'system',
					text: `Task submitted (${resp.task_id}). Routing to Architect for blueprint generation...`,
					time: nowTime()
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
	 */
	reset() {
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
