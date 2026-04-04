<!--
	WorkspaceSelector — compact workspace picker for ChatView.

	Shows the currently selected workspace with a dropdown to switch.
	Displays a lock icon for protected workspaces and inline PIN entry.
	Includes "Add directory" with text input and server-side directory browser.
	Fetched from GET /api/workspaces on mount.

	Issue #106: Planner + chat task creation — Phase A add-directory UI
-->
<script lang="ts">
	import { workspacesStore } from '$lib/stores/workspaces.svelte.js';

	/** Workspace dropdown & selection state. */
	let showDropdown = $state(false);
	let showPinInput = $state(false);
	let pinValue = $state('');
	let verifying = $state(false);

	/** Add-directory state. */
	let showAddInput = $state(false);
	let newDirPath = $state('');
	let addingDir = $state(false);
	let addDirError = $state<string | null>(null);

	/** Browse mode state. */
	let browseMode = $state(false);
	let browsePath = $state('');
	let browseParent = $state<string | null>(null);
	let browseEntries = $state<BrowseEntry[]>([]);
	let browseLoading = $state(false);
	let browseError = $state<string | null>(null);
	let browseUnavailable = $state(false);

	interface BrowseEntry {
		name: string;
		path: string;
		has_children: boolean;
		is_project: boolean;
	}

	/** Reference for auto-focus on the text input. */
	let addInputEl: HTMLInputElement | undefined = $state();
	/** Reference for detecting outside clicks. */
	let selectorEl: HTMLDivElement | undefined = $state();

	function selectWorkspace(path: string) {
		workspacesStore.select(path);
		showDropdown = false;
		resetAddState();
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

	// -- Add directory handlers --

	function openAddInput() {
		showAddInput = true;
		browseMode = false;
		newDirPath = '';
		addDirError = null;
		browseError = null;
		// Auto-focus after DOM update
		requestAnimationFrame(() => addInputEl?.focus());
	}

	function resetAddState() {
		showAddInput = false;
		browseMode = false;
		newDirPath = '';
		addDirError = null;
		addingDir = false;
		browseEntries = [];
		browsePath = '';
		browseParent = null;
		browseError = null;
		browseUnavailable = false;
		browseLoading = false;
	}

	async function confirmAddDirectory() {
		const path = newDirPath.trim();
		if (!path || addingDir) return;
		// Client-side duplicate check
		if (workspacesStore.list.some((w) => w.path === path)) {
			addDirError = 'Already in workspace list';
			return;
		}
		addDirError = null;
		addingDir = true;
		const success = await workspacesStore.addDirectory(path);
		addingDir = false;
		if (success) {
			showDropdown = false;
			resetAddState();
		} else {
			addDirError = workspacesStore.error ?? 'Failed to add directory';
		}
	}

	function handleAddKeydown(e: KeyboardEvent) {
		if (e.key === 'Enter') {
			e.preventDefault();
			confirmAddDirectory();
		}
		if (e.key === 'Escape') {
			e.preventDefault();
			resetAddState();
		}
	}

	// -- Browse mode handlers --

	async function openBrowseMode() {
		browseMode = true;
		browsePath = '';
		browseParent = null;
		browseEntries = [];
		addDirError = null;
		browseError = null;
		await fetchBrowse('');
	}

	async function fetchBrowse(path: string) {
		browseLoading = true;
		browseUnavailable = false;
		browsePath = '';
		browseParent = null;
		browseEntries = [];
		browseError = null;
		try {
			const params = new URLSearchParams();
			if (path) params.set('path', path);
			const res = await fetch(`/api/filesystem/browse?${params.toString()}`);
			const body = await res.json();
			if (res.ok && body.data) {
				browsePath = body.data.current_path ?? path;
				browseParent = body.data.parent_path ?? null;
				browseEntries = (body.data.entries ?? []) as BrowseEntry[];
			} else {
				browseError = body.errors?.[0] ?? 'Failed to browse directory';
			}
		} catch (err) {
			browseUnavailable = true;
			browseError = null;
		} finally {
			browseLoading = false;
		}
	}

	function navigateTo(path: string) {
		fetchBrowse(path);
	}

	function selectBrowsedDir() {
		if (!browsePath) return;
		newDirPath = browsePath;
		browseMode = false;
		confirmAddDirectory();
	}

	/** Split a path into clickable breadcrumb segments. */
	function breadcrumbs(fullPath: string): { label: string; path: string }[] {
		if (!fullPath) return [];
		const isWindows = fullPath.includes('\\') || /^[A-Z]:/i.test(fullPath);
		const sep = isWindows ? '\\' : '/';
		const parts = fullPath.split(sep).filter(Boolean);
		const crumbs: { label: string; path: string }[] = [];
		for (let i = 0; i < parts.length; i++) {
			const path = isWindows
				? parts.slice(0, i + 1).join(sep) + (i === 0 ? sep : '')
				: '/' + parts.slice(0, i + 1).join('/');
			crumbs.push({ label: parts[i], path });
		}
		return crumbs;
	}

	// -- Outside-click dismissal --

	function handleDocumentClick(e: MouseEvent) {
		if (showDropdown && selectorEl && !selectorEl.contains(e.target as Node)) {
			showDropdown = false;
			resetAddState();
		}
	}

	$effect(() => {
		if (showDropdown) {
			document.addEventListener('click', handleDocumentClick, true);
			return () => document.removeEventListener('click', handleDocumentClick, true);
		}
	});
</script>

<div class="workspace-selector" style="font-family: var(--font-mono);" bind:this={selectorEl}>
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

			<!-- Divider -->
			<div class="dropdown-divider"></div>

			{#if !showAddInput}
				<!-- Add directory trigger -->
				<button class="dropdown-item add-trigger" onclick={openAddInput}>
					<span class="add-icon">+</span>
					<span>Add directory</span>
				</button>
			{:else if browseMode}
				<!-- Browse mode -->
				<div class="browse-panel">
					<!-- Breadcrumb -->
					<div class="browse-breadcrumb">
						{#each breadcrumbs(browsePath) as crumb, i}
							{#if i > 0}<span class="crumb-sep">/</span>{/if}
							<button
								class="crumb-btn"
								onclick={() => navigateTo(crumb.path)}
								title={crumb.path}
							>
								{crumb.label}
							</button>
						{/each}
					</div>

					<!-- Directory listing -->
					<div class="browse-list">
						{#if browseLoading}
							<div class="browse-status">Loading…</div>
						{:else if browseUnavailable}
							<div class="browse-status">Browser unavailable — use "Type path" below.</div>
						{:else if browseError}
							<div class="browse-status error">{browseError}</div>
						{:else if browseEntries.length === 0}
							<div class="browse-status">No subdirectories</div>
						{:else}
							{#if browseParent !== null}
								<button
									class="browse-entry"
									onclick={() => navigateTo(browseParent ?? '')}
								>
									<span class="entry-icon">↑</span>
									<span class="entry-name">..</span>
								</button>
							{/if}
							{#each browseEntries as entry}
								<button
									class="browse-entry"
									onclick={() => navigateTo(entry.path)}
									title={entry.path}
								>
									<span class="entry-icon">{entry.is_project ? '📦' : '📁'}</span>
									<span class="entry-name">{entry.name}</span>
									{#if entry.is_project}
										<span class="project-badge">PROJECT</span>
									{/if}
								</button>
							{/each}
						{/if}
					</div>

					<!-- Browse actions -->
					<div class="browse-actions">
						<button class="browse-select" onclick={selectBrowsedDir} disabled={!browsePath || browseLoading || !!browseError || browseUnavailable}>
							Select this directory
						</button>
						<button class="browse-text-btn" onclick={() => { browseMode = false; requestAnimationFrame(() => addInputEl?.focus()); }}>
							Type path
						</button>
						<button class="browse-cancel" onclick={resetAddState}>✕</button>
					</div>
				</div>
			{:else}
				<!-- Text input mode -->
				<div class="add-input-row">
					<input
						bind:this={addInputEl}
						bind:value={newDirPath}
						onkeydown={handleAddKeydown}
						placeholder="/home/user/projects/my-app"
						disabled={addingDir}
						class="add-input"
					/>
					<button
						class="browse-icon-btn"
						onclick={openBrowseMode}
						title="Browse directories"
						disabled={addingDir}
					>
						📂
					</button>
					<button
						class="add-confirm"
						onclick={confirmAddDirectory}
						disabled={addingDir || !newDirPath.trim()}
					>
						{addingDir ? '…' : '✓'}
					</button>
					<button class="add-cancel" onclick={resetAddState}>✕</button>
				</div>
				{#if addDirError}
					<div class="add-error">{addDirError}</div>
				{/if}
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
	{#if workspacesStore.error && !showAddInput}
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
		max-height: 400px;
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
	.dropdown-divider {
		height: 1px;
		background: var(--color-border);
	}

	/* -- Add directory trigger -- */
	.add-trigger {
		color: var(--color-accent-cyan) !important;
		border-bottom: none !important;
	}
	.add-trigger:hover {
		background: var(--color-accent-cyan)08 !important;
	}
	.add-icon {
		font-size: 13px;
		font-weight: 600;
		flex-shrink: 0;
	}

	/* -- Text input mode -- */
	.add-input-row {
		display: flex;
		align-items: center;
		gap: 4px;
		padding: 6px 8px;
	}
	.add-input {
		flex: 1;
		background: var(--color-bg-input);
		border: 1px solid var(--color-border);
		border-radius: 3px;
		padding: 4px 8px;
		color: var(--color-text-bright);
		font-family: var(--font-mono);
		font-size: 11px;
		outline: none;
		min-width: 0;
	}
	.add-input:focus {
		border-color: var(--color-accent-cyan);
	}
	.add-input::placeholder {
		color: var(--color-text-dim);
	}
	.browse-icon-btn {
		background: transparent;
		border: 1px solid var(--color-border);
		border-radius: 3px;
		padding: 3px 6px;
		cursor: pointer;
		font-size: 12px;
		flex-shrink: 0;
		line-height: 1;
	}
	.browse-icon-btn:hover {
		border-color: var(--color-accent-cyan);
		background: var(--color-accent-cyan)08;
	}
	.browse-icon-btn:disabled {
		opacity: 0.4;
		cursor: not-allowed;
	}
	.add-confirm {
		background: #34d39920;
		border: 1px solid #34d39940;
		color: #34d399;
		padding: 3px 10px;
		border-radius: 3px;
		cursor: pointer;
		font-family: var(--font-mono);
		font-size: 12px;
		font-weight: 600;
		flex-shrink: 0;
	}
	.add-confirm:disabled {
		opacity: 0.4;
		cursor: not-allowed;
	}
	.add-cancel {
		background: transparent;
		border: 1px solid var(--color-border);
		color: var(--color-text-dim);
		padding: 3px 8px;
		border-radius: 3px;
		cursor: pointer;
		font-family: var(--font-mono);
		font-size: 12px;
		flex-shrink: 0;
	}
	.add-cancel:hover {
		border-color: #ef4444;
		color: #ef4444;
	}
	.add-error {
		font-size: 10px;
		color: #ef4444;
		padding: 2px 10px 6px;
	}

	/* -- Browse mode -- */
	.browse-panel {
		padding: 6px 8px;
	}
	.browse-breadcrumb {
		display: flex;
		align-items: center;
		flex-wrap: wrap;
		gap: 2px;
		padding: 4px 6px;
		background: var(--color-bg-input);
		border: 1px solid var(--color-border);
		border-radius: 3px;
		margin-bottom: 6px;
		min-height: 26px;
	}
	.crumb-btn {
		background: transparent;
		border: none;
		color: var(--color-accent-cyan);
		font-family: var(--font-mono);
		font-size: 10px;
		cursor: pointer;
		padding: 1px 3px;
		border-radius: 2px;
	}
	.crumb-btn:hover {
		background: var(--color-accent-cyan)15;
	}
	.crumb-sep {
		color: var(--color-text-dim);
		font-size: 10px;
	}
	.browse-list {
		max-height: 200px;
		overflow-y: auto;
		border: 1px solid var(--color-border);
		border-radius: 3px;
		margin-bottom: 6px;
	}
	.browse-entry {
		display: flex;
		align-items: center;
		gap: 6px;
		width: 100%;
		padding: 4px 8px;
		background: transparent;
		border: none;
		border-bottom: 1px solid var(--color-border);
		color: var(--color-text-muted);
		font-family: var(--font-mono);
		font-size: 11px;
		cursor: pointer;
		text-align: left;
	}
	.browse-entry:last-child {
		border-bottom: none;
	}
	.browse-entry:hover {
		background: var(--color-bg-surface);
		color: var(--color-text-bright);
	}
	.entry-icon {
		font-size: 12px;
		flex-shrink: 0;
	}
	.entry-name {
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		flex: 1;
		min-width: 0;
	}
	.project-badge {
		font-size: 8px;
		color: #34d399;
		background: #34d39912;
		border: 1px solid #34d39925;
		padding: 1px 5px;
		border-radius: 2px;
		letter-spacing: 0.5px;
		font-weight: 600;
		flex-shrink: 0;
	}
	.browse-status {
		padding: 10px;
		font-size: 11px;
		color: var(--color-text-dim);
		text-align: center;
	}
	.browse-status.error {
		color: #ef4444;
	}
	.browse-actions {
		display: flex;
		align-items: center;
		gap: 6px;
	}
	.browse-select {
		flex: 1;
		background: #34d39920;
		border: 1px solid #34d39940;
		color: #34d399;
		padding: 5px 10px;
		border-radius: 3px;
		cursor: pointer;
		font-family: var(--font-mono);
		font-size: 10px;
		font-weight: 600;
	}
	.browse-select:disabled {
		opacity: 0.4;
		cursor: not-allowed;
	}
	.browse-text-btn {
		background: transparent;
		border: 1px solid var(--color-border);
		color: var(--color-text-dim);
		padding: 5px 8px;
		border-radius: 3px;
		cursor: pointer;
		font-family: var(--font-mono);
		font-size: 10px;
		flex-shrink: 0;
	}
	.browse-text-btn:hover {
		color: var(--color-accent-cyan);
		border-color: var(--color-accent-cyan);
	}
	.browse-cancel {
		background: transparent;
		border: 1px solid var(--color-border);
		color: var(--color-text-dim);
		padding: 4px 8px;
		border-radius: 3px;
		cursor: pointer;
		font-family: var(--font-mono);
		font-size: 12px;
		flex-shrink: 0;
	}
	.browse-cancel:hover {
		border-color: #ef4444;
		color: #ef4444;
	}

	/* -- PIN -- */
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
