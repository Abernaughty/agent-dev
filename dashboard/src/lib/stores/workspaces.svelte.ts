/**
 * Workspaces store — reactive workspace directory list.
 *
 * Fetches allowed workspaces from GET /api/workspaces.
 * Tracks the currently selected workspace for task creation.
 * Handles PIN verification for protected workspaces.
 *
 * Issue #106: Planner + chat task creation
 */

import type { WorkspaceInfo, VerifyWorkspaceAuthResponse } from '$lib/types/api.js';

let workspaces = $state<WorkspaceInfo[]>([]);
let selectedPath = $state<string>('');
let loading = $state(false);
let error = $state<string | null>(null);

/** PIN verification state for protected workspaces. */
let pinVerified = $state(false);
let pinError = $state<string | null>(null);

export const workspacesStore = {
	get list() {
		return workspaces;
	},
	get selected() {
		return selectedPath;
	},
	get loading() {
		return loading;
	},
	get error() {
		return error;
	},
	get pinVerified() {
		return pinVerified;
	},
	get pinError() {
		return pinError;
	},

	/** Whether the currently selected workspace is protected. */
	get isSelectedProtected(): boolean {
		const ws = workspaces.find((w) => w.path === selectedPath);
		return ws?.is_protected ?? false;
	},

	/** Whether the selected workspace is ready for task creation. */
	get canCreateTask(): boolean {
		if (!selectedPath) return false;
		if (this.isSelectedProtected && !pinVerified) return false;
		return true;
	},

	/** Fetch workspaces from the proxy route. */
	async refresh() {
		loading = true;
		error = null;
		try {
			const res = await fetch('/api/workspaces');
			const body = await res.json();
			if (res.ok && body.data) {
				workspaces = body.data as WorkspaceInfo[];
				// Auto-select default workspace if nothing selected
				if (!selectedPath || !workspaces.some((w) => w.path === selectedPath)) {
					const defaultWs = workspaces.find((w) => w.is_default);
					selectedPath = defaultWs?.path ?? workspaces[0]?.path ?? '';
				}
				// Reset PIN state when workspaces refresh
				pinVerified = false;
				pinError = null;
			} else {
				error = body.errors?.[0] ?? 'Failed to fetch workspaces';
			}
		} catch (err) {
			error = err instanceof Error ? err.message : 'Network error';
		} finally {
			loading = false;
		}
	},

	/** Select a workspace by path. Resets PIN state. */
	select(path: string) {
		selectedPath = path;
		pinVerified = false;
		pinError = null;
	},

	/** Verify PIN for a protected workspace. */
	async verifyPin(pin: string): Promise<boolean> {
		pinError = null;
		try {
			const res = await fetch('/api/workspaces/verify-auth', {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ workspace: selectedPath, pin })
			});
			const body = await res.json();
			if (res.ok && body.data) {
				const result = body.data as VerifyWorkspaceAuthResponse;
				pinVerified = result.authorized;
				if (!result.authorized) {
					pinError = 'Invalid PIN';
				}
				return result.authorized;
			}
			pinError = body.errors?.[0] ?? 'Verification failed';
			return false;
		} catch (err) {
			pinError = err instanceof Error ? err.message : 'Network error';
			return false;
		}
	},

	/** Add a new directory to the allowed list. */
	async addDirectory(path: string): Promise<boolean> {
		try {
			const res = await fetch('/api/workspaces', {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ path })
			});
			const body = await res.json();
			if (res.ok && body.data) {
				workspaces = body.data as WorkspaceInfo[];
				selectedPath = path;
				return true;
			}
			error = body.errors?.[0] ?? 'Failed to add workspace';
			return false;
		} catch (err) {
			error = err instanceof Error ? err.message : 'Network error';
			return false;
		}
	},

	/** Reset to empty state. */
	reset() {
		workspaces = [];
		selectedPath = '';
		error = null;
		pinVerified = false;
		pinError = null;
	}
};
