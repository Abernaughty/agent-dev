/**
 * Memory store — reactive memory entries with batch operations.
 *
 * Initialised by fetching GET /api/memory.
 * Updated from SSE `memory_added` events.
 * Supports approve/reject mutations with optimistic updates.
 * Batch approve/reject for all pending entries.
 * Module-grouped derived for sidebar display.
 * Audit log fetched from backend.
 *
 * Issue #37, #19
 */

import type { MemoryEntry, MemoryStatus, AuditLogEntry } from '$lib/types/api.js';

let entries = $state<MemoryEntry[]>([]);
let loading = $state(false);
let error = $state<string | null>(null);
let auditLog = $state<AuditLogEntry[]>([]);
let auditLoading = $state(false);

/** Group entries by module, sorted: L0-Discovered first within each group. */
function groupByModule(list: MemoryEntry[]): { module: string; entries: MemoryEntry[] }[] {
	const groups = new Map<string, MemoryEntry[]>();
	for (const entry of list) {
		const mod = entry.module || 'global';
		if (!groups.has(mod)) groups.set(mod, []);
		groups.get(mod)!.push(entry);
	}

	const tierPriority = (tier: string): number => {
		if (tier === 'l0-discovered') return 0;
		if (tier === 'l0-core') return 1;
		if (tier === 'l1') return 2;
		return 3;
	};

	const result: { module: string; entries: MemoryEntry[] }[] = [];
	for (const [mod, items] of groups) {
		items.sort((a, b) => {
			const tierDiff = tierPriority(a.tier) - tierPriority(b.tier);
			if (tierDiff !== 0) return tierDiff;
			const statusDiff = (a.status === 'pending' ? 0 : 1) - (b.status === 'pending' ? 0 : 1);
			return statusDiff;
		});
		result.push({ module: mod, entries: items });
	}

	result.sort((a, b) => {
		const aPending = a.entries.filter((e) => e.status === 'pending').length;
		const bPending = b.entries.filter((e) => e.status === 'pending').length;
		return bPending - aPending;
	});

	return result;
}

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
	get pendingEntries() {
		return entries.filter((e) => e.status === 'pending');
	},
	get groupedByModule() {
		return groupByModule(entries);
	},
	get audit() {
		return auditLog;
	},
	get auditLoading() {
		return auditLoading;
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

	/** Fetch audit log from the proxy route. */
	async refreshAudit(limit: number = 100) {
		auditLoading = true;
		try {
			const res = await fetch(`/api/memory/audit?limit=${limit}`);
			const body = await res.json();
			if (res.ok && body.data) {
				auditLog = body.data;
			}
		} catch {
			// Audit log is non-critical — fail silently
		} finally {
			auditLoading = false;
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

	/** Batch approve all pending entries. */
	async batchApprove(): Promise<{ succeeded: number; failed: number }> {
		const pending = entries.filter((e) => e.status === 'pending');
		let succeeded = 0;
		let failed = 0;
		for (const entry of pending) {
			const ok = await this.approve(entry.id);
			if (ok) succeeded++;
			else failed++;
		}
		if (succeeded > 0) this.refreshAudit();
		return { succeeded, failed };
	},

	/** Batch reject all pending entries. */
	async batchReject(): Promise<{ succeeded: number; failed: number }> {
		const pending = entries.filter((e) => e.status === 'pending');
		let succeeded = 0;
		let failed = 0;
		for (const entry of pending) {
			const ok = await this.reject(entry.id);
			if (ok) succeeded++;
			else failed++;
		}
		if (succeeded > 0) this.refreshAudit();
		return { succeeded, failed };
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
		auditLog = [];
	}
};
