<!--
	AgentDetailView — shows detail for a single agent + their activity log.

	Issue #38: Data Integration — PR3
-->
<script lang="ts">
	import { tasksStore } from '$lib/stores/tasks.svelte.js';
	import type { Agent } from '$lib/types/api.js';

	interface Props {
		agent: Agent;
	}

	let { agent }: Props = $props();

	const eventStyles: Record<string, { color: string; label: string }> = {
		plan: { color: 'var(--color-accent-cyan)', label: 'PLAN' },
		code: { color: 'var(--color-accent-purple)', label: 'CODE' },
		exec: { color: 'var(--color-accent-yellow)', label: 'EXEC' },
		fail: { color: 'var(--color-accent-red)', label: 'FAIL' },
		retry: { color: 'var(--color-accent-orange)', label: 'RETRY' },
		success: { color: 'var(--color-accent-green)', label: 'DONE' }
	};

	const agentEvents = $derived(() => {
		const events: { time: string; action: string; type: string; taskId: string }[] = [];
		for (const task of tasksStore.list) {
			for (const ev of task.timeline) {
				if (ev.agent === agent.id) {
					events.push({ time: ev.time, action: ev.action, type: ev.type, taskId: task.id });
				}
			}
		}
		return events;
	});
</script>

<div class="max-w-[700px] p-5 pl-6" style="font-family: var(--font-mono);">
	<div class="mb-5 flex items-center gap-3">
		<div
			class="flex h-10 w-10 items-center justify-center rounded-lg text-[16px] font-semibold"
			style="background: {agent.color}20; border: 1px solid {agent.color}30; color: {agent.color};"
		>
			{agent.name.charAt(0)}
		</div>
		<div>
			<div class="text-[16px]" style="color: var(--color-text-bright);">{agent.name}</div>
			<div class="text-[11px]" style="color: var(--color-text-dim);">{agent.model}</div>
		</div>
		<span
			class="ml-2 rounded-md px-2.5 py-0.5 text-[10px] uppercase"
			style="color: {agent.color}; background: {agent.color}15; letter-spacing: 0.5px;"
		>
			{agent.status}
		</span>
	</div>

	<div class="mb-2.5 text-[10px]" style="color: var(--color-text-dim); letter-spacing: 1px;">ACTIVITY LOG</div>

	{#if agentEvents().length === 0}
		<div class="text-[11px]" style="color: var(--color-text-faint);">No activity in current tasks.</div>
	{:else}
		{#each agentEvents() as ev}
			{@const style = eventStyles[ev.type] ?? { color: 'var(--color-text-dim)', label: '?' }}
			<div class="mb-2 flex items-start gap-2.5">
				<span class="min-w-[34px] pt-0.5 text-[10px]" style="color: var(--color-text-faint);">{ev.time}</span>
				<span
					class="min-w-[36px] rounded-sm px-1.5 py-0.5 text-center text-[8px] font-semibold"
					style="color: {style.color}; background: {style.color}18; letter-spacing: 0.5px;"
				>{style.label}</span>
				<span class="text-[11px] leading-relaxed" style="color: var(--color-text-muted);">{ev.action}</span>
			</div>
		{/each}
	{/if}
</div>
