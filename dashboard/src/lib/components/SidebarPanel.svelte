<!--
	SidebarPanel — store-driven sidebar with selection tracking.

	Reads from agentsStore, tasksStore, memoryStore, prsStore to
	populate entries dynamically. Selection state is managed via
	selectedId prop and onSelect callback.

	Issue #38: Data Integration — PR3
	Issue #19: Memory entries grouped by module via memoryStore.groupedByModule
	Issue #143: P4 — status indicators use shape + color + text label (not color-only dots)
-->
<script lang="ts">
	import { agentsStore } from '$lib/stores/agents.svelte.js';
	import { tasksStore } from '$lib/stores/tasks.svelte.js';
	import { memoryStore } from '$lib/stores/memory.svelte.js';
	import { prsStore } from '$lib/stores/prs.svelte.js';

	type PanelId = 'agents' | 'memory' | 'prs' | 'chat';

	interface Props {
		activePanel: PanelId | null;
		selectedId: string;
		onSelect: (id: string) => void;
	}

	let { activePanel, selectedId, onSelect }: Props = $props();

	const titles: Record<PanelId, string> = {
		agents: 'Agent Dashboard',
		memory: 'Memory Approvals',
		prs: 'Pull Requests',
		chat: 'Task Chat'
	};

	/** P4: Status indicator config — shape glyph + color per status. */
	const statusConfig: Record<string, { color: string; glyph: string }> = {
		idle: { color: 'var(--color-text-dim)', glyph: '\u2014' },
		planning: { color: 'var(--color-accent-cyan)', glyph: '\u27F3' },
		coding: { color: 'var(--color-accent-purple)', glyph: '\u27F3' },
		reviewing: { color: 'var(--color-accent-yellow)', glyph: '\u27F3' },
		waiting: { color: 'var(--color-accent-yellow)', glyph: '\u25E6' },
		error: { color: 'var(--color-accent-red)', glyph: '\u2715' }
	};

	const aliveStatuses = ['planning', 'coding', 'reviewing'];

	const hasCompletedTask = $derived(tasksStore.list.some((t) => t.completed_at));
</script>

{#if activePanel}
	<div
		class="flex w-[260px] shrink-0 flex-col overflow-hidden border-r"
		style="background: var(--color-bg-sidebar); border-color: var(--color-border);"
	>
		<div class="border-b px-3 py-2" style="border-color: var(--color-border);">
			<span class="text-[10px] uppercase tracking-widest" style="color: var(--color-text-muted); font-family: var(--font-mono);">{titles[activePanel]}</span>
		</div>

		<div class="flex-1 overflow-y-auto py-1" style="font-family: var(--font-mono);">

			{#if activePanel === 'agents'}
				<button onclick={() => onSelect('__timeline')} class="flex w-full items-center gap-2 border-l-2 px-2.5 py-1.5 text-left transition-colors hover:bg-[var(--color-bg-hover)]" style="border-color: {selectedId === '__timeline' ? 'var(--color-accent-cyan)' : 'transparent'}; background: {selectedId === '__timeline' ? 'var(--color-bg-surface)' : 'transparent'};">
					<div class="min-w-0 flex-1">
						<div class="truncate text-[11px]" style="color: {selectedId === '__timeline' ? 'var(--color-text-bright)' : 'var(--color-text-muted)'};">Task Timeline</div>
						<div class="mt-0.5 truncate text-[9px]" style="color: var(--color-text-dim);">All agents</div>
					</div>
				</button>
				<div class="mx-2 my-1" style="height: 1px; background: var(--color-border);"></div>
				<div class="px-2.5 py-1"><span class="text-[9px] uppercase" style="color: var(--color-text-faint); letter-spacing: 0.8px;">Blueprints</span></div>
				{#each tasksStore.list as task (task.id)}
					<button onclick={() => onSelect(`task-${task.id}`)} class="flex w-full items-center gap-2 border-l-2 px-2.5 py-1.5 text-left transition-colors hover:bg-[var(--color-bg-hover)]" style="border-color: {selectedId === `task-${task.id}` ? 'var(--color-accent-cyan)' : 'transparent'}; background: {selectedId === `task-${task.id}` ? 'var(--color-bg-surface)' : 'transparent'};">
						<div class="min-w-0 flex-1">
							<div class="truncate text-[11px]" style="color: {selectedId === `task-${task.id}` ? 'var(--color-text-bright)' : 'var(--color-text-muted)'};">{task.description.length > 35 ? task.description.slice(0, 35) + '...' : task.description}</div>
							<div class="mt-0.5 truncate text-[9px]" style="color: var(--color-text-dim);">{task.id} | {task.status}</div>
						</div>
						<span class="shrink-0 rounded-sm px-1.5 py-px text-[8px] uppercase" style="color: {task.status === 'passed' ? 'var(--color-accent-green)' : task.status === 'failed' ? 'var(--color-accent-red)' : 'var(--color-accent-yellow)'}; background: {task.status === 'passed' ? 'var(--color-accent-green)' : task.status === 'failed' ? 'var(--color-accent-red)' : 'var(--color-accent-yellow)'}12;">{task.status}</span>
					</button>
				{/each}
				<div class="mx-2 my-1" style="height: 1px; background: var(--color-border);"></div>
				<div class="px-2.5 py-1"><span class="text-[9px] uppercase" style="color: var(--color-text-faint); letter-spacing: 0.8px;">Agents</span></div>
				{#each agentsStore.list as agent (agent.id)}
					{@const cfg = statusConfig[agent.status] ?? { color: 'var(--color-text-dim)', glyph: '?' }}
					<button onclick={() => onSelect(`agent-${agent.id}`)} class="flex w-full items-center gap-2 border-l-2 px-2.5 py-1.5 text-left transition-colors hover:bg-[var(--color-bg-hover)]" style="border-color: {selectedId === `agent-${agent.id}` ? agent.color : 'transparent'}; background: {selectedId === `agent-${agent.id}` ? 'var(--color-bg-surface)' : 'transparent'};">
						<div class="min-w-0 flex-1">
							<div class="truncate text-[11px]" style="color: {selectedId === `agent-${agent.id}` ? 'var(--color-text-bright)' : 'var(--color-text-muted)'};">{agent.name}</div>
							<div class="mt-0.5 truncate text-[9px]" style="color: var(--color-text-dim);">{agent.model}</div>
						</div>
						<!-- P4: Status indicator — shape + color + text label -->
						<div class="flex shrink-0 items-center gap-1.5">
							<span
								class="flex h-4 w-4 items-center justify-center rounded text-[11px]"
								style="background: {cfg.color}20; border: 1px solid {cfg.color}40; color: {cfg.color}; animation: {aliveStatuses.includes(agent.status) ? 'pulse 1.5s ease-in-out infinite' : 'none'};"
							>{cfg.glyph}</span>
							<span class="text-[9px]" style="color: {cfg.color};">{agent.status}</span>
						</div>
					</button>
				{/each}
				<div class="mx-2 my-1" style="height: 1px; background: var(--color-border);"></div>
				<div class="px-2.5 py-1"><span class="text-[9px] uppercase" style="color: var(--color-text-faint); letter-spacing: 0.8px;">Session</span></div>
				<button onclick={() => onSelect('__debrief')} class="flex w-full items-center gap-2 border-l-2 px-2.5 py-1.5 text-left transition-colors hover:bg-[var(--color-bg-hover)]" style="border-color: {selectedId === '__debrief' ? 'var(--color-accent-green)' : 'transparent'}; background: {selectedId === '__debrief' ? 'var(--color-bg-surface)' : 'transparent'};">
					<div class="min-w-0 flex-1">
						<div class="truncate text-[11px]" style="color: {selectedId === '__debrief' ? 'var(--color-text-bright)' : 'var(--color-text-muted)'};">Session Debrief</div>
						<div class="mt-0.5 truncate text-[9px]" style="color: var(--color-text-dim);">Latest task summary</div>
					</div>
					{#if hasCompletedTask}
						<span class="shrink-0 rounded-sm px-1.5 py-px text-[7px] font-semibold" style="color: var(--color-accent-green); background: var(--color-accent-green)12; border: 1px solid var(--color-accent-green)25;">DONE</span>
					{/if}
				</button>
				<button onclick={() => onSelect('__costs')} class="flex w-full items-center gap-2 border-l-2 px-2.5 py-1.5 text-left transition-colors hover:bg-[var(--color-bg-hover)]" style="border-color: {selectedId === '__costs' ? 'var(--color-accent-amber)' : 'transparent'}; background: {selectedId === '__costs' ? 'var(--color-bg-surface)' : 'transparent'};">
					<div class="min-w-0 flex-1">
						<div class="truncate text-[11px]" style="color: {selectedId === '__costs' ? 'var(--color-text-bright)' : 'var(--color-text-muted)'};">Cost Tracker</div>
						<div class="mt-0.5 truncate text-[9px]" style="color: var(--color-text-dim);">Aggregate spending</div>
					</div>
				</button>

			{:else if activePanel === 'memory'}
				<button onclick={() => onSelect('__memory-home')} class="flex w-full items-center gap-2 border-l-2 px-2.5 py-1.5 text-left transition-colors hover:bg-[var(--color-bg-hover)]" style="border-color: {selectedId === '__memory-home' ? 'var(--color-accent-cyan)' : 'transparent'}; background: {selectedId === '__memory-home' ? 'var(--color-bg-surface)' : 'transparent'};">
					<div class="min-w-0 flex-1">
						<div class="truncate text-[11px]" style="color: {selectedId === '__memory-home' ? 'var(--color-text-bright)' : 'var(--color-text-muted)'};">Overview</div>
						<div class="mt-0.5 truncate text-[9px]" style="color: var(--color-text-dim);">{memoryStore.pendingCount} pending</div>
					</div>
				</button>
				<div class="mx-2 my-1" style="height: 1px; background: var(--color-border);"></div>
				<button onclick={() => onSelect('__memory-audit')} class="flex w-full items-center gap-2 border-l-2 px-2.5 py-1.5 text-left transition-colors hover:bg-[var(--color-bg-hover)]" style="border-color: {selectedId === '__memory-audit' ? 'var(--color-accent-cyan)' : 'transparent'}; background: {selectedId === '__memory-audit' ? 'var(--color-bg-surface)' : 'transparent'};">
					<div class="min-w-0 flex-1">
						<div class="truncate text-[11px]" style="color: {selectedId === '__memory-audit' ? 'var(--color-text-bright)' : 'var(--color-text-muted)'};">Audit Log</div>
						<div class="mt-0.5 truncate text-[9px]" style="color: var(--color-text-dim);">Approval history</div>
					</div>
				</button>
				<div class="mx-2 my-1" style="height: 1px; background: var(--color-border);"></div>
				{#each memoryStore.groupedByModule as group (group.module)}
					{@const groupPending = group.entries.filter(e => e.status === 'pending').length}
					<div class="flex items-center justify-between px-2.5 py-1">
						<span class="text-[9px] uppercase" style="color: var(--color-text-faint); letter-spacing: 0.8px;">{group.module}</span>
						{#if groupPending > 0}
							<span class="min-w-[14px] rounded-full px-1 text-center text-[8px] font-semibold" style="background: var(--color-accent-amber)20; color: var(--color-accent-amber);">{groupPending}</span>
						{/if}
					</div>
					{#each group.entries as entry (entry.id)}
						{@const tierColor = entry.tier.includes('l0') ? 'var(--color-accent-amber)' : 'var(--color-accent-purple)'}
						<button onclick={() => onSelect(entry.id)} class="flex w-full items-center gap-2 border-l-2 px-2.5 py-1.5 text-left transition-colors hover:bg-[var(--color-bg-hover)]" style="border-color: {selectedId === entry.id ? tierColor : 'transparent'}; background: {selectedId === entry.id ? 'var(--color-bg-surface)' : 'transparent'}; opacity: {entry.status !== 'pending' ? 0.4 : 1};">
							<div class="min-w-0 flex-1">
								<div class="truncate text-[11px]" style="color: {selectedId === entry.id ? 'var(--color-text-bright)' : 'var(--color-text-muted)'};">{entry.content.length > 40 ? entry.content.slice(0, 40) + '...' : entry.content}</div>
								<div class="mt-0.5 truncate text-[9px]" style="color: var(--color-text-dim);">{entry.tier} | {entry.source_agent}</div>
							</div>
							{#if entry.status === 'pending'}
								<span class="inline-block h-1.5 w-1.5 shrink-0 rounded-full" style="background: var(--color-accent-amber);"></span>
							{/if}
						</button>
					{/each}
				{/each}

			{:else if activePanel === 'prs'}
				<button onclick={() => onSelect('__pr-home')} class="flex w-full items-center gap-2 border-l-2 px-2.5 py-1.5 text-left transition-colors hover:bg-[var(--color-bg-hover)]" style="border-color: {selectedId === '__pr-home' ? 'var(--color-accent-cyan)' : 'transparent'}; background: {selectedId === '__pr-home' ? 'var(--color-bg-surface)' : 'transparent'};">
					<div class="min-w-0 flex-1">
						<div class="truncate text-[11px]" style="color: {selectedId === '__pr-home' ? 'var(--color-text-bright)' : 'var(--color-text-muted)'};">Overview</div>
						<div class="mt-0.5 truncate text-[9px]" style="color: var(--color-text-dim);">{prsStore.openCount} open</div>
					</div>
				</button>
				<div class="mx-2 my-1" style="height: 1px; background: var(--color-border);"></div>
				{#each prsStore.list as pr (pr.id)}
					<button onclick={() => onSelect(`pr-${pr.id}`)} class="flex w-full items-center gap-2 border-l-2 px-2.5 py-1.5 text-left transition-colors hover:bg-[var(--color-bg-hover)]" style="border-color: {selectedId === `pr-${pr.id}` ? (pr.status === 'merged' ? 'var(--color-accent-green)' : 'var(--color-accent-yellow)') : 'transparent'}; background: {selectedId === `pr-${pr.id}` ? 'var(--color-bg-surface)' : 'transparent'};">
						<div class="min-w-0 flex-1">
							<div class="truncate text-[11px]" style="color: {selectedId === `pr-${pr.id}` ? 'var(--color-text-bright)' : 'var(--color-text-muted)'};">{pr.id} {pr.title}</div>
							<div class="mt-0.5 truncate text-[9px]" style="color: var(--color-text-dim);">{pr.status} | +{pr.additions} -{pr.deletions}</div>
						</div>
					</button>
				{/each}

			{:else if activePanel === 'chat'}
				<button onclick={() => onSelect('__chat')} class="flex w-full items-center gap-2 border-l-2 px-2.5 py-1.5 text-left transition-colors hover:bg-[var(--color-bg-hover)]" style="border-color: var(--color-accent-cyan); background: var(--color-bg-surface);">
					<div class="min-w-0 flex-1">
						<div class="truncate text-[11px]" style="color: var(--color-text-bright);">Full Conversation</div>
						<div class="mt-0.5 truncate text-[9px]" style="color: var(--color-text-dim);">Current task</div>
					</div>
				</button>
			{/if}
		</div>
	</div>
{/if}
