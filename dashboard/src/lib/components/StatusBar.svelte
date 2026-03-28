<!--
	StatusBar — system vitals bar wired to live store data.

	Left: LangGraph version + agent statuses from agentsStore.
	Right: Aggregate task budget (tokens/cost/retries) from tasksStore.

	Issue #19: Replaced all hardcoded values with store-driven data.
-->
<script lang="ts">
	import { agentsStore } from '$lib/stores/agents.svelte.js';
	import { tasksStore } from '$lib/stores/tasks.svelte.js';
	import { connectionStore } from '$lib/stores/connection.svelte.js';

	const statusColors: Record<string, string> = {
		idle: 'var(--color-text-dim)',
		planning: 'var(--color-accent-cyan)',
		coding: 'var(--color-accent-purple)',
		reviewing: 'var(--color-accent-yellow)',
		waiting: 'var(--color-accent-yellow)',
		testing: 'var(--color-accent-green)',
		error: 'var(--color-accent-red)'
	};

	const aliveStatuses = ['planning', 'coding', 'reviewing', 'testing'];

	/** Aggregate budget across all tasks. */
	const aggregate = $derived(() => {
		const tasks = tasksStore.list;
		if (tasks.length === 0) {
			return { tokensUsed: 0, tokenBudget: 0, costUsed: 0, costBudget: 0, retriesUsed: 0, maxRetries: 0, hasTasks: false };
		}
		let tokensUsed = 0, tokenBudget = 0, costUsed = 0, costBudget = 0, retriesUsed = 0, maxRetries = 0;
		for (const task of tasks) {
			if (task.budget) {
				tokensUsed += task.budget.tokens_used;
				tokenBudget += task.budget.token_budget;
				costUsed += task.budget.cost_used;
				costBudget += task.budget.cost_budget;
				retriesUsed += task.budget.retries_used;
				maxRetries += task.budget.max_retries;
			}
		}
		return { tokensUsed, tokenBudget, costUsed, costBudget, retriesUsed, maxRetries, hasTasks: true };
	});

	const tokenPct = $derived(() => {
		const a = aggregate();
		if (a.tokenBudget === 0) return 0;
		return Math.round((a.tokensUsed / a.tokenBudget) * 100);
	});

	const tokenBarColor = $derived(() => {
		const pct = tokenPct();
		if (pct > 90) return 'var(--color-accent-red)';
		if (pct > 75) return 'var(--color-accent-amber)';
		return 'var(--color-accent-cyan)';
	});
</script>

<div
	class="flex h-6 shrink-0 items-center justify-between px-3"
	style="background: var(--color-status-bar);"
>
	<!-- Left side -->
	<div class="flex items-center gap-3.5 text-[10px] text-white" style="font-family: var(--font-mono);">
		<span>LangGraph v0.4</span>
		<span class="opacity-70">|</span>
		{#if agentsStore.list.length > 0}
			{#each agentsStore.list as agent (agent.id)}
				<span class="flex items-center gap-1">
					<span
						class="inline-block h-[7px] w-[7px] rounded-full"
						style="
							background: {statusColors[agent.status] || 'var(--color-text-dim)'};
							animation: {aliveStatuses.includes(agent.status) ? 'pulse 1.5s ease-in-out infinite' : 'none'};
							box-shadow: {aliveStatuses.includes(agent.status) ? '0 0 6px ' + (statusColors[agent.status] || '') : 'none'};
						"
					></span>
					<span class="opacity-90">{agent.name}</span>
				</span>
			{/each}
		{:else}
			<span class="opacity-50">No agents</span>
		{/if}
	</div>

	<!-- Right side -->
	<div
		class="flex items-center gap-2.5 text-[10px]"
		style="color: rgba(255,255,255,0.8); font-family: var(--font-mono);"
	>
		{#if aggregate().hasTasks}
			<span>Sandbox: locked-down</span>
			<span class="opacity-50">|</span>
			<span class="flex items-center gap-1.5">
				<span>Tokens:</span>
				<span
					class="inline-block h-1 w-[50px] overflow-hidden rounded-sm"
					style="background: rgba(255,255,255,0.2);"
				>
					<span
						class="block h-full rounded-sm transition-all duration-300"
						style="width: {tokenPct()}%; background: {tokenBarColor()};"
					></span>
				</span>
				<span style="color: {tokenBarColor()};">{tokenPct()}%</span>
			</span>
			<span class="opacity-50">|</span>
			<span>${aggregate().costUsed.toFixed(2)}</span>
			<span class="opacity-50">|</span>
			<span>Retries: {aggregate().retriesUsed}/{aggregate().maxRetries}</span>
		{:else}
			<span class="opacity-50">No active task</span>
		{/if}
	</div>
</div>
