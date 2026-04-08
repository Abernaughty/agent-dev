/**
 * Workspaces store — reactive workspace directory list.
 *
 * Fetches allowed workspaces from GET /api/workspaces.
 * Tracks the currently selected workspace for task creation.
 * Handles PIN verification for protected workspaces.
 *
 * Issue #106: Planner + chat task creation
 * Issue #153: Remote GitHub workspace support
 */

import type { WorkspaceInfo, WorkspaceType, VerifyWorkspaceAuthResponse } from '$lib/types/api.js';

let workspaces = $state<WorkspaceInfo[]>([]);
let selectedPath = $state<string>('');
let loading = $state(false);
let error = $state<string | null>(null);

/** PIN verification state for protected workspaces. */
let pinVerified = $state(false);
let pinError = $state<string | null>(null);
/** Stores the verified PIN so it can be forwarded to task creation. */
let storedPin = $state<string | null>(null);

/** Issue #153: Remote workspace state. */
let workspaceType = $state<WorkspaceType>('local');
let githubRepo = $state<string>('');
let githubBranch = $state<string>('main');
let githubFeatureBranch = $state<string>('');
let githubTokenEnvVar = $state<string>('GITHUB_TOKEN');

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
	/** The verified PIN value for forwarding to task creation. */
	get verifiedPin(): string | null {
		return storedPin;
	},

	/** Issue #153: Remote workspace getters/setters. */
	get workspaceType() {
		return workspaceType;
	},
	get githubRepo() {
		return githubRepo;
	},
	get githubBranch() {
		return githubBranch;
	},
	get githubFeatureBranch() {
		return githubFeatureBranch;
	},
	get githubTokenEnvVar() {
		return githubTokenEnvVar;
	},

	setWorkspaceType(type: WorkspaceType) {
		workspaceType = type;
		if (type === 'local') {
			// Reset remote fields when switching back to local
			githubRepo = '';
			githubBranch = 'main';
			githubFeatureBranch = '';
			githubTokenEnvVar = 'GITHUB_TOKEN';
		}
	},
	setGithubRepo(repo: string) {
		githubRepo = repo;
	},
	setGithubBranch(branch: string) {
		githubBranch = branch;
	},
	setGithubFeatureBranch(branch: string) {
		githubFeatureBranch = branch;
	},
	setGithubTokenEnvVar(envVar: string) {
		githubTokenEnvVar = envVar;
	},

	/** Whether the remote workspace config is valid for task creation. */
	get isRemoteReady(): boolean {
		if (workspaceType !== 'github') return true;
		return githubRepo.includes('/') && githubRepo.length > 2;
	},

	/** Whether the currently selected workspace is protected. */
	get isSelectedProtected(): boolean {
		const ws = workspaces.find((w) => w.path === selectedPath);
		return ws?.is_protected ?? false;
	},

	/** Whether the selected workspace is ready for task creation. */
	get canCreateTask(): boolean {
		if (workspaceType === 'github') return this.isRemoteReady;
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
				storedPin = null;
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
		storedPin = null;
	},

	/** Verify PIN for a protected workspace. Stores PIN on success for task creation. */
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
				if (result.authorized) {
					storedPin = pin;
				} else {
					pinError = 'Invalid PIN';
					storedPin = null;
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
				this.select(path);
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
		loading = false;
		error = null;
		pinVerified = false;
		pinError = null;
		storedPin = null;
		workspaceType = 'local';
		githubRepo = '';
		githubBranch = 'main';
		githubFeatureBranch = '';
		githubTokenEnvVar = 'GITHUB_TOKEN';
	}
};
