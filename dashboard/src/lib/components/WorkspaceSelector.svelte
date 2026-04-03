<!--
	WorkspaceSelector — compact workspace picker for ChatView.

	Shows the currently selected workspace with a dropdown to switch.
	Displays a lock icon for protected workspaces and inline PIN entry.
	Fetched from GET /api/workspaces on mount.

	Issue #106: Planner + chat task creation
-->
<script lang="ts">
	import { workspacesStore } from '$lib/stores/workspaces.svelte.js';

	let showDropdown = $state(false);
	let showPinInput = $state(false);
	let pinValue = $state('');
	let verifying = $state(false);

	function selectWorkspace(path: string) {
		workspacesStore.select(path);
		showDropdown = false;
		// If protected, prompt for PIN
		if (workspacesStore.isSelectedProtected) {
			showPinInput = true;
			pinValue = '';
		} else {
			showPinInput = false;
		}
	}

	async function verifyPin() {
		if (!pinValue.trim()) return;
		verifying = true;
		await workspacesStore.verifyPin(pinValue.trim());
		verifying = false;
		if (workspacesStore.pinVerified) {
			showPinInput = false;
			pinValue = '';
		}
	}

	function handlePinKeydown(e: KeyboardEvent) {
		if (e.key === 'Enter') {
			e.preventDefault();
			verifyPin();
		}
		if (e.key === 'Escape') {
			showPinInput = false;
			pinValue = '';
		}
	}

	/** Shorten a workspace path for display. */
	function displayPath(path: string): string {
		if (!path) return 'No workspace';
		const parts = path.replace(/\\/g, '/').split('/');
		return parts.length > 2 ? `.../${parts.slice(-2).join('/')}` : path;
	}
</script>

<div class="workspace-selector" style="font-family: var(--font-mono);">
	<!-- Selector bar -->
	<div class="selector-bar">
		<span class="label">WORKSPACE</span>
		<button
			class="selector-button"
			onclick={() => (showDropdown = !showDropdown)}
			title={workspacesStore.selected || 'Select workspace'}
		>
			{#if workspacesStore.isSelectedProtected}
				<span class="lock-icon">🔒</span>
			{/if}
			<span class="path-text">{displayPath(workspacesStore.selected)}</span>
			<span class="chevron">{showDropdown ? '▲' : '▼'}</span>
		</button>
		{#if workspacesStore.isSelectedProtected && !workspacesStore.pinVerified}
			<button
				class="pin-trigger"
				onclick={() => {
					showPinInput = !showPinInput;
					pinValue = '';
				}}
				title="PIN required"
			>
				PIN
			</button>
		{/if}
		{#if workspacesStore.isSelectedProtected && workspacesStore.pinVerified}
			<span class="verified-badge">✓</span>
		{/if}
	</div>

	<!-- Dropdown -->
	{#if showDropdown}
		<div class="dropdown">
			{#each workspacesStore.list as ws}
				<button
					class="dropdown-item"
					class:active={ws.path === workspacesStore.selected}
					onclick={() => selectWorkspace(ws.path)}
				>
					<span class="item-path">
						{#if ws.is_protected}<span class="lock-icon">🔒</span>{/if}
						{displayPath(ws.path)}
					</span>
					{#if ws.is_default}
						<span class="default-badge">DEFAULT</span>
					{/if}
				</button>
			{/each}
			{#if workspacesStore.list.length === 0}
				<div class="dropdown-empty">No workspaces configured</div>
			{/if}
		</div>
	{/if}

	<!-- PIN input -->
	{#if showPinInput && workspacesStore.isSelectedProtected && !workspacesStore.pinVerified}
		<div class="pin-row">
			<span class="pin-label">PIN:</span>
			<input
				type="password"
				bind:value={pinValue}
				onkeydown={handlePinKeydown}
				placeholder="Enter PIN"
				disabled={verifying}
				class="pin-input"
			/>
			<button class="pin-submit" onclick={verifyPin} disabled={verifying || !pinValue.trim()}>
				{verifying ? '...' : '→'}
			</button>
			{#if workspacesStore.pinError}
				<span class="pin-error">{workspacesStore.pinError}</span>
			{/if}
		</div>
	{/if}

	<!-- Loading / error -->
	{#if workspacesStore.loading}
		<div class="status-msg">Loading workspaces...</div>
	{/if}
	{#if workspacesStore.error}
		<div class="status-msg error">{workspacesStore.error}</div>
	{/if}
</div>

<style>
	.workspace-selector {
		border-bottom: 1px solid var(--color-border);
		padding: 6px 16px;
		position: relative;
	}
	.selector-bar {
		display: flex;
		align-items: center;
		gap: 8px;
	}
	.label {
		font-size: 9px;
		color: var(--color-text-dim);
		letter-spacing: 1px;
		flex-shrink: 0;
	}
	.selector-button {
		display: flex;
		align-items: center;
		gap: 6px;
		background: var(--color-bg-input);
		border: 1px solid var(--color-border);
		border-radius: 4px;
		padding: 4px 8px;
		color: var(--color-text-bright);
		font-family: var(--font-mono);
		font-size: 11px;
		cursor: pointer;
		flex: 1;
		min-width: 0;
		text-align: left;
	}
	.selector-button:hover {
		border-color: var(--color-accent-cyan);
	}
	.path-text {
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		flex: 1;
	}
	.chevron {
		font-size: 8px;
		color: var(--color-text-dim);
		flex-shrink: 0;
	}
	.lock-icon {
		font-size: 10px;
		flex-shrink: 0;
	}
	.pin-trigger {
		font-family: var(--font-mono);
		font-size: 9px;
		background: #f59e0b15;
		border: 1px solid #f59e0b25;
		color: #f59e0b;
		padding: 2px 8px;
		border-radius: 3px;
		cursor: pointer;
		letter-spacing: 0.5px;
		font-weight: 600;
	}
	.verified-badge {
		font-size: 11px;
		color: #34d399;
		font-weight: 600;
	}
	.dropdown {
		position: absolute;
		top: 100%;
		left: 16px;
		right: 16px;
		background: var(--color-bg-sidebar);
		border: 1px solid var(--color-border);
		border-radius: 4px;
		z-index: 50;
		max-height: 200px;
		overflow-y: auto;
		box-shadow: 0 4px 12px rgba(0, 0, 0, 0.4);
	}
	.dropdown-item {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 8px;
		width: 100%;
		padding: 6px 10px;
		background: transparent;
		border: none;
		border-bottom: 1px solid var(--color-border);
		color: var(--color-text-muted);
		font-family: var(--font-mono);
		font-size: 11px;
		cursor: pointer;
		text-align: left;
	}
	.dropdown-item:last-child {
		border-bottom: none;
	}
	.dropdown-item:hover {
		background: var(--color-bg-surface);
		color: var(--color-text-bright);
	}
	.dropdown-item.active {
		color: var(--color-accent-cyan);
		background: var(--color-bg-surface);
	}
	.item-path {
		display: flex;
		align-items: center;
		gap: 6px;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		min-width: 0;
	}
	.default-badge {
		font-size: 8px;
		color: var(--color-text-dim);
		background: var(--color-bg-input);
		padding: 1px 5px;
		border-radius: 2px;
		letter-spacing: 0.5px;
		flex-shrink: 0;
	}
	.dropdown-empty {
		padding: 8px 10px;
		font-size: 11px;
		color: var(--color-text-dim);
		font-style: italic;
	}
	.pin-row {
		display: flex;
		align-items: center;
		gap: 6px;
		margin-top: 6px;
	}
	.pin-label {
		font-size: 9px;
		color: #f59e0b;
		letter-spacing: 0.5px;
		flex-shrink: 0;
	}
	.pin-input {
		flex: 1;
		background: var(--color-bg-input);
		border: 1px solid #f59e0b40;
		border-radius: 3px;
		padding: 3px 8px;
		color: var(--color-text-bright);
		font-family: var(--font-mono);
		font-size: 11px;
		outline: none;
		max-width: 160px;
	}
	.pin-input:focus {
		border-color: #f59e0b;
	}
	.pin-submit {
		background: #f59e0b20;
		border: 1px solid #f59e0b40;
		color: #f59e0b;
		padding: 3px 10px;
		border-radius: 3px;
		cursor: pointer;
		font-family: var(--font-mono);
		font-size: 11px;
		font-weight: 600;
	}
	.pin-submit:disabled {
		opacity: 0.4;
		cursor: not-allowed;
	}
	.pin-error {
		font-size: 10px;
		color: #ef4444;
	}
	.status-msg {
		font-size: 10px;
		color: var(--color-text-dim);
		margin-top: 4px;
	}
	.status-msg.error {
		color: #ef4444;
	}
</style>
