<!--
	MainContent — routes to the correct content view based on
	the active sidebar panel and selected item.

	Issue #38: Data Integration — PR3
	Issue #19: Added batch approve/reject buttons, audit log viewer
	Updated: Added ChatView, SessionDebrief, CostView
	Issue #108: Replaced BlueprintView with TaskDetailView for task-{id}
-->
<script lang="ts">
	import { agentsStore } from '$lib/stores/agents.svelte.js';
	import { tasksStore } from '$lib/stores/tasks.svelte.js';
	import { memoryStore } from '$lib/stores/memory.svelte.js';
	import { prsStore } from '$lib/stores/prs.svelte.js';
	import TimelineView from './views/TimelineView.svelte';
	import AgentDetailView from './views/AgentDetailView.svelte';
	import MemoryDetailView from './views/MemoryDetailView.svelte';
	import PRDetailView from './views/PRDetailView.svelte';
	import TaskDetailView from './views/TaskDetailView.svelte';
	import ChatView from './views/ChatView.svelte';
	import SessionDebrief from './views/SessionDebrief.svelte';
	import CostView from './views/CostView.svelte';

	type PanelId = 'agents' | 'memory' | 'prs' | 'chat';

	interface Props {
		activePanel: PanelId | null;
		selectedId: string;
	}

	let { activePanel, selectedId }: Props = $props();

	// -- Batch operations state --
	let batchInProgress = $state(false);
	let batchResult = $state<{ type: 'approve' | 'reject'; succeeded: number; failed: number } | null>(null);
	let confirmRejectAll = $state(false);

	async function handleBatchApprove() {
		batchInProgress = true;
		batchResult = null;
		const result = await memoryStore.batchApprove();
		batchResult = { type: 'approve', ...result };
		batchInProgress = false;
	}

	async function handleBatchReject() {
		if (!confirmRejectAll) {
			confirmRejectAll = true;
			return;
		}
		confirmRejectAll = false;
		batchInProgress = true;
		batchResult = null;
		const result = await memoryStore.batchReject();
		batchResult = { type: 'reject', ...result };
		batchInProgress = false;
	}

	function cancelRejectConfirm() {
		confirmRejectAll = false;
	}

	// -- Memory entry lookup: supports any id format, not just 'mem-' prefix --
	const selectedMemoryEntry = $derived(
		activePanel === 'memory' && selectedId !== '__memory-home' && selectedId !== '__memory-audit'
			? memoryStore.list.find((e) => e.id === selectedId)
			: undefined
	);
</script>

{#if activePanel === 'agents'}
	{#if selectedId.startsWith('agent-')}
		{@const agentId = selectedId.replace('agent-', '')}
		{@const agent = agentsStore.list.find((a) => a.id === agentId)}
		{#if agent}
			<AgentDetailView {agent} />
		{/if}
	{:else if selectedId.startsWith('task-')}
		<!-- Issue #108: TaskDetailView replaces BlueprintView -->
		<TaskDetailView taskId={selectedId.replace('task-', '')} />
	{:else if selectedId === '__debrief'}
		<SessionDebrief />
	{:else if selectedId === '__costs'}
		<CostView />
	{:else}
		<TimelineView />
	{/if}

{:else if activePanel === 'memory'}
	{#if selectedMemoryEntry}
		<MemoryDetailView entry={selectedMemoryEntry} />
	{:else if selectedId === '__memory-audit'}
		<!-- Audit Log View -->
		<div class="p-4 pl-6" style="font-family: var(--font-mono);">
			<div class="mb-3.5 text-[10px]" style="color: var(--color-text-faint); letter-spacing: 1px;">AUDIT LOG</div>
			<div class="mb-4 text-[12px] leading-relaxed" style="color: var(--color-text-muted);">
				History of all memory approval and rejection actions.
			</div>
			{#if memoryStore.auditLoading}
				<div class="text-[11px]" style="color: var(--color-text-dim);">Loading audit log...</div>
			{:else if memoryStore.audit.length === 0}
				<div class="text-[11px]" style="color: var(--color-text-dim);">No audit entries yet. Approve or reject memory entries to see history here.</div>
			{:else}
				<div class="flex flex-col gap-1">
					{#each memoryStore.audit as log (log.id)}
						{@const isApprove = log.action === 'approve'}
						<div class="flex items-start gap-3 rounded-md border px-3 py-2" style="background: var(--color-bg-activity); border-color: var(--color-border); border-left: 2px solid {isApprove ? 'var(--color-accent-green)' : 'var(--color-accent-red)'};">
							<span class="shrink-0 rounded-sm px-1.5 py-px text-[8px] font-semibold uppercase" style="color: {isApprove ? 'var(--color-accent-green)' : 'var(--color-accent-red)'}; background: {isApprove ? 'var(--color-accent-green)' : 'var(--color-accent-red)'}12;">
								{log.action}
							</span>
							<div class="min-w-0 flex-1">
								<div class="truncate text-[11px]" style="color: var(--color-text-muted);">{log.entry_content}</div>
								<div class="mt-0.5 text-[9px]" style="color: var(--color-text-dim);">{log.entry_tier} | {log.entry_module} | {log.timestamp}</div>
							</div>
						</div>
					{/each}
				</div>
			{/if}
		</div>
	{:else}
		<!-- Memory Overview with batch actions -->
		<div class="p-4 pl-6" style="font-family: var(--font-mono);">
			<div class="mb-3.5 text-[10px]" style="color: var(--color-text-faint); letter-spacing: 1px;">MEMORY OVERVIEW</div>
			<div class="mb-4 text-[12px] leading-relaxed" style="color: var(--color-text-muted);">
				Select a memory entry from the sidebar to inspect details and approve or reject.
			</div>

			<!-- Batch Actions -->
			{#if memoryStore.pendingCount > 0}
				<div class="mb-4 flex items-center gap-2.5">
					<button onclick={handleBatchApprove} disabled={batchInProgress} class="cursor-pointer rounded-md border px-4 py-1.5 text-[11px] font-medium transition-opacity disabled:cursor-not-allowed disabled:opacity-50" style="background: var(--color-accent-green)15; border-color: var(--color-accent-green)30; color: var(--color-accent-green);">
						{batchInProgress ? 'Processing...' : `Approve All (${memoryStore.pendingCount})`}
					</button>
					{#if confirmRejectAll}
						<span class="text-[11px]" style="color: var(--color-accent-red);">Are you sure?</span>
						<button onclick={handleBatchReject} disabled={batchInProgress} class="cursor-pointer rounded-md border px-4 py-1.5 text-[11px] font-medium transition-opacity disabled:cursor-not-allowed disabled:opacity-50" style="background: var(--color-accent-red)15; border-color: var(--color-accent-red)30; color: var(--color-accent-red);">
							Yes, Reject All
						</button>
						<button onclick={cancelRejectConfirm} class="cursor-pointer rounded-md border px-4 py-1.5 text-[11px] transition-opacity" style="background: transparent; border-color: var(--color-border); color: var(--color-text-dim);">
							Cancel
						</button>
					{:else}
						<button onclick={handleBatchReject} disabled={batchInProgress} class="cursor-pointer rounded-md border px-4 py-1.5 text-[11px] transition-opacity disabled:cursor-not-allowed disabled:opacity-50" style="background: var(--color-accent-red)10; border-color: var(--color-accent-red)20; color: var(--color-accent-red);">
							Reject All ({memoryStore.pendingCount})
						</button>
					{/if}
				</div>
			{/if}

			<!-- Batch Result Feedback -->
			{#if batchResult}
				<div class="mb-4 rounded-md border px-3 py-2 text-[11px]" style="background: {batchResult.type === 'approve' ? 'var(--color-accent-green)' : 'var(--color-accent-red)'}08; border-color: {batchResult.type === 'approve' ? 'var(--color-accent-green)' : 'var(--color-accent-red)'}20; color: {batchResult.type === 'approve' ? 'var(--color-accent-green)' : 'var(--color-accent-red)'};">
					{batchResult.succeeded} {batchResult.type === 'approve' ? 'approved' : 'rejected'}{batchResult.failed > 0 ? `, ${batchResult.failed} failed` : ''}
				</div>
			{/if}

			<!-- Stats -->
			<div class="flex gap-4">
				{#each [
					{ label: 'Pending', count: memoryStore.pendingCount, color: 'var(--color-accent-amber)' },
					{ label: 'L0-Discovered', count: memoryStore.list.filter(e => e.tier === 'l0-discovered').length, color: 'var(--color-accent-amber)' },
					{ label: 'L1', count: memoryStore.list.filter(e => e.tier === 'l1').length, color: 'var(--color-accent-purple)' },
					{ label: 'Total', count: memoryStore.list.length, color: 'var(--color-text-muted)' }
				] as stat}
					<div class="rounded-md border px-4 py-3 text-center" style="background: var(--color-bg-activity); border-color: var(--color-border);">
						<div class="text-[20px] font-semibold" style="color: {stat.color};">{stat.count}</div>
						<div class="mt-0.5 text-[9px]" style="color: var(--color-text-dim);">{stat.label}</div>
					</div>
				{/each}
			</div>
		</div>
	{/if}

{:else if activePanel === 'prs'}
	{#if selectedId.startsWith('pr-')}
		{@const prId = selectedId.replace('pr-', '')}
		{@const pr = prsStore.list.find((p) => p.id === prId)}
		{#if pr}
			<PRDetailView {pr} />
		{/if}
	{:else}
		<div class="p-4 pl-6" style="font-family: var(--font-mono);">
			<div class="mb-3.5 text-[10px]" style="color: var(--color-text-faint); letter-spacing: 1px;">PULL REQUESTS</div>
			<div class="mb-4 text-[12px] leading-relaxed" style="color: var(--color-text-muted);">
				Select a pull request from the sidebar to view details and test results.
			</div>
			<div class="flex gap-4">
				{#each [
					{ label: 'Open', count: prsStore.openCount, color: 'var(--color-accent-yellow)' },
					{ label: 'Merged', count: prsStore.list.filter(p => p.status === 'merged').length, color: 'var(--color-accent-green)' }
				] as stat}
					<div class="rounded-md border px-4 py-3 text-center" style="background: var(--color-bg-activity); border-color: var(--color-border);">
						<div class="text-[20px] font-semibold" style="color: {stat.color};">{stat.count}</div>
						<div class="mt-0.5 text-[9px]" style="color: var(--color-text-dim);">{stat.label}</div>
					</div>
				{/each}
			</div>
		</div>
	{/if}

{:else if activePanel === 'chat'}
	<ChatView />

{:else}
	<TimelineView />
{/if}
