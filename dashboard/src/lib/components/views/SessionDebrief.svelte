<!--
	SessionDebrief — standalone session debrief card.

	Shows a complete summary after a task finishes:
	  - Metrics grid: tokens, cost, retries, agent calls
	  - Outputs grid: PRs opened, merged, memory pending, tests

	Can be used inline in TimelineView (already done) or as
	a standalone view when selected from the sidebar.

	Relates to #17
-->
<script lang="ts">
	import { tasksStore } from '$lib/stores/tasks.svelte.js';
	import { memoryStore } from '$lib/stores/memory.svelte.js';
	import { prsStore } from '$lib/stores/prs.svelte.js';

	const task = $derived(() => {
		const list = tasksStore.list;
		const completed = list.filter((t) => t.completed_at);
		return completed.length > 0 ? completed[completed.length - 1] : list.length > 0 ? list[list.length - 1] : null;
	});

	const metrics = $derived(() => {
		const t = task();
		if (!t) return [];
		return [
			{
				label: 'Tokens Used',
				value: t.budget.tokens_used.toLocaleString(),
				sub: `of ${t.budget.token_budget.toLocaleString()} budget (${Math.round((t.budget.tokens_used / t.budget.token_budget) * 100)}%)`,
				color:
					t.budget.tokens_used / t.budget.token_budget > 0.75
						? 'var(--color-accent-amber)'
						: 'var(--color-text-bright)',
				icon: 'Tk'
			},
			{
				label: 'Estimated Cost',
				value: `$${t.budget.cost_used.toFixed(2)}`,
				sub: `of $${t.budget.cost_budget} budget`,
				color: 'var(--color-text-bright)',
				icon: '$'
			},
			{
				label: 'Retries',
				value: String(t.budget.retries_used),
				sub: `of ${t.budget.max_retries} max`,
				color: 'var(--color-text-bright)',
				icon: 'Rt'
			},
			{
				label: 'Agent Calls',
				value: String(t.timeline.length),
				sub: 'timeline events',
				color: 'var(--color-text-bright)',
				icon: 'Ag'
			}
		];
	});

	const outputs = $derived(() => {
		const openPRs = prsStore.list.filter((p) => p.status === 'review').length;
		const mergedPRs = prsStore.list.filter((p) => p.status === 'merged').length;
		const pendingMem = memoryStore.pendingCount;
		const t = task();
		const testEvents = t?.timeline.filter((e) => e.type === 'success') ?? [];

		return [
			{
				label: 'PRs Open',
				value: String(openPRs),
				detail: openPRs > 0 ? 'Awaiting review' : 'None',
				color: 'var(--color-accent-cyan)'
			},
			{
				label: 'PRs Merged',
				value: String(mergedPRs),
				detail: mergedPRs > 0 ? 'Merged to main' : 'None yet',
				color: mergedPRs > 0 ? 'var(--color-accent-green)' : 'var(--color-text-dim)'
			},
			{
				label: 'Memory Pending',
				value: String(pendingMem),
				detail: pendingMem > 0 ? 'Needs approval' : 'All clear',
				color: pendingMem > 0 ? 'var(--color-accent-amber)' : 'var(--color-accent-green)'
			},
			{
				label: 'Passing',
				value: testEvents.length > 0 ? 'Yes' : '\u2014',
				detail: testEvents.length > 0 ? 'All tests green' : 'No test results',
				color: testEvents.length > 0 ? 'var(--color-accent-green)' : 'var(--color-text-dim)'
			}
		];
	});
</script>

<div class="p-4 pl-6" style="font-family: var(--font-mono);">
	<div
		class="mb-3.5 flex items-center gap-2 text-[10px]"
		style="color: var(--color-text-faint); letter-spacing: 1px;"
	>
		<span>SESSION DEBRIEF</span>
		<span class="flex-1" style="height: 1px; background: var(--color-border);"></span>
		{#if task()}
			<span style="color: var(--color-text-dim); letter-spacing: 0;">{task()!.description}</span>
		{/if}
	</div>

	{#if task()}
		<div
			class="overflow-hidden rounded-lg border"
			style="background: var(--color-bg-activity); border-color: var(--color-border);"
		>
			<!-- Header -->
			<div
				class="flex items-center justify-between border-b px-3.5 py-2.5"
				style="background: var(--color-bg-surface); border-color: var(--color-border);"
			>
				<div class="flex items-center gap-2">
					<span class="text-[10px]" style="color: var(--color-text-dim); letter-spacing: 1px;"
						>SESSION DEBRIEF</span
					>
					<span
						class="rounded-sm px-1.5 py-px text-[8px]"
						style="color: {task()!.completed_at
							? 'var(--color-accent-green)'
							: 'var(--color-accent-amber)'};
							background: {task()!.completed_at
							? 'var(--color-accent-green)'
							: 'var(--color-accent-amber)'}12;"
					>
						{task()!.completed_at ? 'COMPLETE' : 'IN PROGRESS'}
					</span>
				</div>
				<span class="text-[9px]" style="color: var(--color-text-faint);">{task()!.id}</span>
			</div>

			<div class="p-3.5">
				<!-- Metrics grid -->
				<div class="mb-3.5 grid grid-cols-4 gap-2.5">
					{#each metrics() as metric}
						<div
							class="rounded-md border p-2.5"
							style="background: var(--color-bg-primary); border-color: var(--color-border);"
						>
							<div class="mb-1 flex items-center justify-between">
								<span
									class="text-[8px]"
									style="color: var(--color-text-dim); letter-spacing: 0.5px;"
									>{metric.label}</span
								>
								<span
									class="text-[8px] font-semibold"
									style="color: var(--color-text-faint);">{metric.icon}</span
								>
							</div>
							<div class="text-[18px] font-semibold" style="color: {metric.color};">
								{metric.value}
							</div>
							<div class="mt-0.5 text-[8px]" style="color: var(--color-text-dim);">
								{metric.sub}
							</div>
						</div>
					{/each}
				</div>

				<!-- Outputs grid -->
				<div class="grid grid-cols-2 gap-2.5">
					{#each outputs() as out}
						<div
							class="flex items-center gap-2.5 rounded-md border p-2 px-2.5"
							style="background: var(--color-bg-primary); border-color: var(--color-border);"
						>
							<div
								class="min-w-[30px] text-center text-[18px] font-semibold"
								style="color: {out.color};"
							>
								{out.value}
							</div>
							<div>
								<div class="text-[10px]" style="color: var(--color-text-muted);">
									{out.label}
								</div>
								<div class="text-[9px]" style="color: var(--color-text-dim);">
									{out.detail}
								</div>
							</div>
						</div>
					{/each}
				</div>
			</div>
		</div>
	{:else}
		<div class="flex h-64 flex-col items-center justify-center gap-3">
			<div class="text-[13px]" style="color: var(--color-text-muted);">No completed tasks</div>
			<div class="text-[11px]" style="color: var(--color-text-dim);">
				Session debrief will appear after a task completes.
			</div>
		</div>
	{/if}
</div>
