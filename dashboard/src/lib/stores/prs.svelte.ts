/**
 * Pull Requests store — reactive PR list.
 *
 * Initialised by fetching GET /api/prs.
 * Polling-based refresh (PRs don't stream via SSE).
 *
 * Issue #37
 */

import type { PullRequest } from '$lib/types/api.js';

let prs = $state<PullRequest[]>([]);
let loading = $state(false);
let error = $state<string | null>(null);

/** Polling interval in ms (30 seconds). */
const POLL_INTERVAL = 30_000;
let pollTimer: ReturnType<typeof setInterval> | null = null;

export const prsStore = {
	get list() {
		return prs;
	},
	get loading() {
		return loading;
	},
	get error() {
		return error;
	},
	get openCount() {
		return prs.filter((p) => p.status === 'review' || p.status === 'open').length;
	},

	/** Fetch PRs from the proxy route. */
	async refresh() {
		loading = true;
		error = null;
		try {
			const res = await fetch('/api/prs');
			const body = await res.json();
			if (res.ok && body.data) {
				prs = body.data;
			} else {
				error = body.errors?.[0] ?? 'Failed to fetch PRs';
			}
		} catch (err) {
			error = err instanceof Error ? err.message : 'Network error';
		} finally {
			loading = false;
		}
	},

	/** Start polling for PR updates. */
	startPolling() {
		this.stopPolling();
		pollTimer = setInterval(() => this.refresh(), POLL_INTERVAL);
	},

	/** Stop polling. */
	stopPolling() {
		if (pollTimer) {
			clearInterval(pollTimer);
			pollTimer = null;
		}
	},

	/** Reset to empty state. */
	reset() {
		prs = [];
		error = null;
		this.stopPolling();
	},

	/** Load mock data directly (used when PUBLIC_USE_MOCK_DATA=true). */
	loadMock(data: PullRequest[]) {
		prs = data;
		loading = false;
		error = null;
	}
};
