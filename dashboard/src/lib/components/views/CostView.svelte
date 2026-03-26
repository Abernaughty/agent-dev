<!--
	CostView — aggregate cost tracking across all orchestrator runs.

	Reads from tasksStore to aggregate token usage, cost, and
	retry counts. Provides a dashboard-level view of spending.

	Relates to #17, #20
-->
<script lang="ts">
	import { tasksStore } from '$lib/stores/tasks.svelte.js';

	const totals = $derived(() => {
		const tasks = tasksStore.list;
		let tokens = 0;
		let cost = 0;
		let retries = 0;
		let completed = 0;
		let failed = 0;

		for (const t of tasks) {
			tokens += t.budget.tokens_used;
			cost += t.budget.cost_used;
			retries += t.budget.retries_used;
			if (t.status === 'passed') completed++;
			if (t.status === 'failed' || t.status === 'escalated') failed++;
		}

		return { tokens, cost, retries, completed, failed, total: tasks.length };
	});

	const avgCostPerTask = $derived(() => {
		const t = totals();
		return t.total > 0 ? t.cost / t.total : 0;
	});

	const avgTokensPerTask = $derived(() => {
		const t = totals();
		return t.total > 0 ? Math.round(t.tokens / t.total) : 0;
	});

	const successRate = $derived(() => {
		const t = totals();
		const finished = t.completed + t.failed;
		return finished > 0 ? Math.round((t.completed / finished) * 100) : 0;
	});
</script>

<div class="p-4 pl-6" style="font-family: var(--font-mono);">
	<div
		class="mb-3.5 flex items-center gap-2 text-[10px]"
		style="color: var(--color-text-faint); letter-spacing: 1px;"
	>
		<span>COST TRACKER</span>
		<span class="flex-1" style="height: 1px; background: var(--color-border);"></span>
		<span style="color: var(--color-text-dim); letter-spacing: 0;">{totals().total} tasks</span>
	</div>

	{#if totals().total > 0}
		<!-- Summary cards -->
		<div class="mb-4 grid grid-cols-4 gap-3">
			{#each [
				{
					label: 'Total Cost',
					value: `$${totals().cost.toFixed(2)}`,
					sub: `avg $${avgCostPerTask().toFixed(3)}/task`,
					color: 'var(--color-text-bright)',
					icon: '$'
				},
				{
					label: 'Total Tokens',
					value: totals().tokens.toLocaleString(),
					sub: `avg ${avgTokensPerTask().toLocaleString()}/task`,
					color: 'var(--color-accent-cyan)',
					icon: 'Tk'
				},
				{
					label: 'Total Retries',
					value: String(totals().retries),
					sub: `across ${totals().total} tasks`,
					color: totals().retries > 0 ? 'var(--color-accent-orange)' : 'var(--color-text-bright)',
					icon: 'Rt'
				},
				{
					label: 'Success Rate',
					value: `${successRate()}%`,
					sub: `${totals().completed} passed / ${totals().failed} failed`,
					color: successRate() >= 80
						? 'var(--color-accent-green)'
						: successRate() >= 50
							? 'var(--color-accent-amber)'
							: 'var(--color-accent-red)',
					icon: '%'
				}
			] as card}
				<div
					class="rounded-md border p-3"
					style="background: var(--color-bg-activity); border-color: var(--color-border);"
				>
					<div class="mb-1 flex items-center justify-between">
						<span
							class="text-[8px]"
							style="color: var(--color-text-dim); letter-spacing: 0.5px;"
							>{card.label}</span
						>
						<span class="text-[8px] font-semibold" style="color: var(--color-text-faint);"
							>{card.icon}</span
						>
					</div>
					<div class="text-[22px] font-semibold" style="color: {card.color};">{card.value}</div>
					<div class="mt-0.5 text-[8px]" style="color: var(--color-text-dim);">{card.sub}</div>
				</div>
			{/each}
		</div>

		<!-- Per-task breakdown -->
		<div
			class="text-[10px]"
			style="color: var(--color-text-dim); letter-spacing: 1px; margin-bottom: 8px;"
		>
			PER-TASK BREAKDOWN
		</div>
		<div
			class="overflow-hidden rounded-md border"
			style="background: var(--color-bg-activity); border-color: var(--color-border);"
		>
			<!-- Header row -->
			<div
				class="grid grid-cols-[1fr_80px_80px_60px_70px] gap-2 border-b px-3 py-2"
				style="border-color: var(--color-border);"
			>
				<span class="text-[9px] font-semibold" style="color: var(--color-text-dim);">Task</span>
				<span
					class="text-right text-[9px] font-semibold"
					style="color: var(--color-text-dim);">Tokens</span
				>
				<span
					class="text-right text-[9px] font-semibold"
					style="color: var(--color-text-dim);">Cost</span
				>
				<span
					class="text-right text-[9px] font-semibold"
					style="color: var(--color-text-dim);">Retries</span
				>
				<span
					class="text-right text-[9px] font-semibold"
					style="color: var(--color-text-dim);">Status</span
				>
			</div>
			<!-- Data rows -->
			{#each tasksStore.list as task}
				<div
					class="grid grid-cols-[1fr_80px_80px_60px_70px] gap-2 border-b px-3 py-2"
					style="border-color: var(--color-border);"
				>
					<span
						class="truncate text-[10px]"
						style="color: var(--color-text-muted);"
						title={task.description}>{task.description}</span
					>
					<span class="text-right text-[10px]" style="color: var(--color-accent-cyan);"
						>{(task.budget.tokens_used / 1000).toFixed(1)}k</span
					>
					<span class="text-right text-[10px]" style="color: var(--color-text-bright);"
						>${task.budget.cost_used.toFixed(3)}</span
					>
					<span
						class="text-right text-[10px]"
						style="color: {task.budget.retries_used > 0
							? 'var(--color-accent-orange)'
							: 'var(--color-text-dim)'};"
					>
						{task.budget.retries_used}/{task.budget.max_retries}
					</span>
					<span
						class="text-right text-[10px] uppercase"
						style="color: {task.status === 'passed'
							? 'var(--color-accent-green)'
							: task.status === 'failed'
								? 'var(--color-accent-red)'
								: 'var(--color-accent-amber)'};"
					>
						{task.status}
					</span>
				</div>
			{/each}
		</div>
	{:else}
		<div class="flex h-64 flex-col items-center justify-center gap-3">
			<div class="text-[13px]" style="color: var(--color-text-muted);">No cost data yet</div>
			<div class="text-[11px]" style="color: var(--color-text-dim);">
				Cost tracking will populate as tasks are executed.
			</div>
		</div>
	{/if}
</div>
