/**
 * Memory store — reactive memory entries.
 *
 * Initialised by fetching GET /api/memory.
 * Updated from SSE `memory_added` events.
 * Supports approve/reject mutations with optimistic updates.
 *
 * Issue #37
 */

import type { MemoryEntry, MemoryStatus } from '$lib/types/api.js';

let entries = $state<MemoryEntry[]>([]);
let loading = $state(false);
let error = $state<string | null>(null);

export const memoryStore = {
	get list() {
		return entries;
	},
	get loading() {
		return loading;
	},
	get error() {
		return error;
	},
	get pendingCount() {
		return entries.filter((e) => e.status === 'pending').length;
	},

	/** Fetch memory entries from the proxy route. */
	async refresh(tier?: string, status?: string) {
		loading = true;
		error = null;
		try {
			const params = new URLSearchParams();
			if (tier) params.set('tier', tier);
			if (status) params.set('status', status);
			const qs = params.toString();
			const url = qs ? `/api/memory?${qs}` : '/api/memory';

			const res = await fetch(url);
			const body = await res.json();
			if (res.ok && body.data) {
				entries = body.data;
			} else {
				error = body.errors?.[0] ?? 'Failed to fetch memory';
			}
		} catch (err) {
			error = err instanceof Error ? err.message : 'Network error';
		} finally {
			loading = false;
		}
	},

	/** Approve a memory entry — optimistic update with rollback. */
	async approve(entryId: string): Promise<boolean> {
		const prev = entries.find((e) => e.id === entryId);
		if (!prev) return false;

		entries = entries.map((e) =>
			e.id === entryId
				? { ...e, status: 'approved' as MemoryStatus, verified: true, expires_at: null, hours_remaining: null }
				: e
		);

		try {
			const res = await fetch(`/api/memory/${entryId}`, {
				method: 'PATCH',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ action: 'approve' })
			});
			if (res.ok) {
				const body = await res.json();
				if (body.data) {
					entries = entries.map((e) => (e.id === entryId ? body.data : e));
				}
				return true;
			}
			entries = entries.map((e) => (e.id === entryId ? prev : e));
			const body = await res.json();
			error = body.errors?.[0] ?? 'Failed to approve';
			return false;
		} catch (err) {
			entries = entries.map((e) => (e.id === entryId ? prev : e));
			error = err instanceof Error ? err.message : 'Network error';
			return false;
		}
	},

	/** Reject a memory entry — optimistic update with rollback. */
	async reject(entryId: string): Promise<boolean> {
		const prev = entries.find((e) => e.id === entryId);
		if (!prev) return false;

		entries = entries.map((e) =>
			e.id === entryId ? { ...e, status: 'rejected' as MemoryStatus } : e
		);

		try {
			const res = await fetch(`/api/memory/${entryId}`, {
				method: 'PATCH',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ action: 'reject' })
			});
			if (res.ok) return true;
			entries = entries.map((e) => (e.id === entryId ? prev : e));
			const body = await res.json();
			error = body.errors?.[0] ?? 'Failed to reject';
			return false;
		} catch (err) {
			entries = entries.map((e) => (e.id === entryId ? prev : e));
			error = err instanceof Error ? err.message : 'Network error';
			return false;
		}
	},

	/** Apply an SSE memory_added event. */
	handleSSE(data: { id: string; tier: string; agent: string; content: string; status: string }) {
		const idx = entries.findIndex((e) => e.id === data.id);
		if (idx >= 0) {
			entries = entries.map((e) =>
				e.id === data.id ? { ...e, status: data.status as MemoryStatus } : e
			);
		}
	},

	/** Reset to empty state. */
	reset() {
		entries = [];
		error = null;
	}
};
