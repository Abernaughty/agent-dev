<!--
	StatusBar — system vitals bar wired to live store data.

	Left: LangGraph version + agent statuses from agentsStore.
	Right: Aggregate task budget (tokens/cost/retries) from tasksStore.

	Issue #19: Replaced all hardcoded values with store-driven data.
	Issue #143: P4 — status indicators use shape + color + text label (not color-only dots)
-->
<script lang="ts">
	import { agentsStore } from '$lib/stores/agents.svelte.js';
	import { tasksStore } from '$lib/stores/tasks.svelte.js';

	/** P4: Status indicator config — shape glyph + color per status. */
	const statusConfig: Record<string, { color: string; glyph: string }> = {
		idle: { color: '#708090', glyph: '\u2014' },
		planning: { color: '#22d3ee', glyph: '\u27F3' },
		coding: { color: '#a78bfa', glyph: '\u27F3' },
		reviewing: { color: '#fbbf24', glyph: '\u27F3' },
		waiting: { color: '#fbbf24', glyph: '\u25E6' },
		testing: { color: '#34d399', glyph: '\u27F3' },
		error: { color: '#ef4444', glyph: '\u2715' }
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
		if (pct > 90) return '#ef4444';
		if (pct > 75) return '#f59e0b';
		return '#22d3ee';
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
				{@const cfg = statusConfig[agent.status] ?? { color: '#708090', glyph: '?' }}
				<span class="flex items-center gap-1">
					<!-- P4: 16px rounded square with shape glyph -->
					<span
						class="flex h-3.5 w-3.5 items-center justify-center rounded-sm text-[9px]"
						style="
							background: {cfg.color}30;
							color: {cfg.color};
							animation: {aliveStatuses.includes(agent.status) ? 'pulse 1.5s ease-in-out infinite' : 'none'};
						"
					>{cfg.glyph}</span>
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
