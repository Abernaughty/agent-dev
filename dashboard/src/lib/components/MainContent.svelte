<!--
	MainContent — routes to the correct content view based on
	the active sidebar panel and selected item.

	Issue #38: Data Integration — PR3 + PR4
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
	import BlueprintView from './views/BlueprintView.svelte';
	import ChatView from './views/ChatView.svelte';

	type PanelId = 'agents' | 'memory' | 'prs' | 'chat';

	interface Props {
		activePanel: PanelId | null;
		selectedId: string;
	}

	let { activePanel, selectedId }: Props = $props();
</script>

{#if activePanel === 'agents'}
	{#if selectedId.startsWith('agent-')}
		{@const agentId = selectedId.replace('agent-', '')}
		{@const agent = agentsStore.list.find((a) => a.id === agentId)}
		{#if agent}
			<AgentDetailView {agent} />
		{/if}
	{:else if selectedId.startsWith('task-')}
		<BlueprintView taskId={selectedId.replace('task-', '')} />
	{:else}
		<TimelineView />
	{/if}

{:else if activePanel === 'memory'}
	{#if selectedId.startsWith('mem-')}
		{@const entry = memoryStore.list.find((e) => e.id === selectedId)}
		{#if entry}
			<MemoryDetailView {entry} />
		{/if}
	{:else}
		<div class="p-4 pl-6" style="font-family: var(--font-mono);">
			<div class="mb-3.5 text-[10px]" style="color: var(--color-text-faint); letter-spacing: 1px;">MEMORY OVERVIEW</div>
			<div class="mb-4 text-[12px] leading-relaxed" style="color: var(--color-text-muted);">
				Select a memory entry from the sidebar to inspect details and approve or reject.
			</div>
			<div class="flex gap-4">
				{#each [
					{ label: 'Pending', count: memoryStore.pendingCount, color: 'var(--color-accent-amber)' },
					{ label: 'L0-Discovered', count: memoryStore.list.filter(e => e.tier === 'l0-discovered').length, color: 'var(--color-accent-amber)' },
					{ label: 'L1', count: memoryStore.list.filter(e => e.tier === 'l1').length, color: 'var(--color-accent-purple)' }
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
