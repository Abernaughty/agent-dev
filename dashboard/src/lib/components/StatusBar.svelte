<!--
	StatusBar — bottom chrome bar showing live system state.

	Reads from:
	- agentsStore.list     → agent dots + statuses
	- tasksStore.list      → token budget, cost, retries (latest task)
	- connection.status    → connection indicator dot

	Issue #38: Data Integration — PR2 StatusBar + ActivityBar
-->
<script lang="ts">
	import { agentsStore } from '$lib/stores/agents.svelte.js';
	import { tasksStore } from '$lib/stores/tasks.svelte.js';
	import { connection } from '$lib/stores/connection.svelte.js';
	import type { AgentStatus } from '$lib/types/api.js';

	const statusColors: Record<string, string> = {
		idle: 'var(--color-text-dim)',
		planning: 'var(--color-accent-cyan)',
		coding: 'var(--color-accent-purple)',
		reviewing: 'var(--color-accent-yellow)',
		waiting: 'var(--color-accent-yellow)',
		testing: 'var(--color-accent-green)',
		error: 'var(--color-accent-red)'
	};

	const aliveStatuses: AgentStatus[] = ['planning', 'coding', 'reviewing'];

	// Latest task budget (most recent task, or fallback zeros)
	const latestBudget = $derived(() => {
		const list = tasksStore.list;
		if (list.length === 0) {
			return { tokens_used: 0, token_budget: 0, retries_used: 0, max_retries: 3, cost_used: 0, cost_budget: 1.0 };
		}
		// Prefer the active task; otherwise the most recent
		const active = tasksStore.activeTasks;
		const task = active.length > 0 ? active[active.length - 1] : list[list.length - 1];
		return task.budget;
	});

	const tokenPct = $derived(() => {
		const b = latestBudget();
		return b.token_budget > 0 ? Math.round((b.tokens_used / b.token_budget) * 100) : 0;
	});

	const barColor = $derived(() => {
		const pct = tokenPct();
		if (pct > 90) return 'var(--color-accent-red)';
		if (pct > 75) return 'var(--color-accent-amber)';
		return 'var(--color-accent-cyan)';
	});

	const connectionDotColor = $derived(() => {
		switch (connection.status) {
			case 'connected': return 'var(--color-accent-green)';
			case 'reconnecting': return 'var(--color-accent-amber)';
			default: return 'var(--color-accent-red)';
		}
	});
</script>

<div
	class="flex h-6 shrink-0 items-center justify-between px-3"
	style="background: var(--color-status-bar);"
>
	<!-- Left side -->
	<div class="flex items-center gap-3.5 text-[10px] text-white" style="font-family: var(--font-mono);">
		<!-- Connection indicator -->
		<span class="flex items-center gap-1">
			<span
				class="inline-block h-[7px] w-[7px] rounded-full"
				style="
					background: {connectionDotColor()};
					animation: {connection.status === 'reconnecting' ? 'pulse 1.5s ease-in-out infinite' : 'none'};
				"
			></span>
			<span class="opacity-90">LangGraph</span>
		</span>
		<span class="opacity-70">|</span>
		{#each agentsStore.list as agent (agent.id)}
			<span class="flex items-center gap-1">
				<span
					class="inline-block h-[7px] w-[7px] rounded-full"
					style="
						background: {statusColors[agent.status] || 'var(--color-text-dim)'};
						animation: {aliveStatuses.includes(agent.status) ? 'pulse 1.5s ease-in-out infinite' : 'none'};
						box-shadow: {aliveStatuses.includes(agent.status) ? '0 0 6px ' + statusColors[agent.status] : 'none'};
					"
				></span>
				<span class="opacity-90">{agent.name}</span>
			</span>
		{/each}
		{#if agentsStore.list.length === 0}
			<span class="opacity-50">No agents</span>
		{/if}
	</div>

	<!-- Right side -->
	<div
		class="flex items-center gap-2.5 text-[10px]"
		style="color: rgba(255,255,255,0.8); font-family: var(--font-mono);"
	>
		<span>Sandbox: locked-down</span>
		<span class="opacity-50">|</span>
		{#if latestBudget().token_budget > 0}
			<span class="flex items-center gap-1.5">
				<span>Tokens:</span>
				<span
					class="inline-block h-1 w-[50px] overflow-hidden rounded-sm"
					style="background: rgba(255,255,255,0.2);"
				>
					<span
						class="block h-full rounded-sm transition-all duration-300"
						style="width: {tokenPct()}%; background: {barColor()};"
					></span>
				</span>
				<span style="color: {barColor()};">{tokenPct()}%</span>
			</span>
			<span class="opacity-50">|</span>
			<span>${latestBudget().cost_used.toFixed(2)}</span>
			<span class="opacity-50">|</span>
			<span>Retries: {latestBudget().retries_used}/{latestBudget().max_retries}</span>
		{:else}
			<span class="opacity-50">No active task</span>
		{/if}
	</div>
</div>
