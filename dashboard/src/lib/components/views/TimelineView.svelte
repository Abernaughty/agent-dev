<!--
	TimelineView — task timeline showing events, budget bar, and session debrief.

	Reads from tasksStore + agentsStore to render the timeline for
	the most recent (or active) task.

	Issue #38: Data Integration — PR3
-->
<script lang="ts">
	import { tasksStore } from '$lib/stores/tasks.svelte.js';
	import { agentsStore } from '$lib/stores/agents.svelte.js';
	import type { TimelineEvent, TaskBudget } from '$lib/types/api.js';

	const currentTask = $derived(() => {
		const active = tasksStore.activeTasks;
		if (active.length > 0) return active[active.length - 1];
		const list = tasksStore.list;
		return list.length > 0 ? list[list.length - 1] : null;
	});

	const agentMap = $derived(() => {
		const map: Record<string, { name: string; color: string }> = {};
		for (const a of agentsStore.list) {
			map[a.id] = { name: a.name, color: a.color };
		}
		return map;
	});

	function agentInfo(agentId: string) {
		return agentMap()[agentId] ?? { name: agentId, color: 'var(--color-text-dim)' };
	}

	const eventStyles: Record<string, { color: string; label: string }> = {
		plan: { color: 'var(--color-accent-cyan)', label: 'PLAN' },
		code: { color: 'var(--color-accent-purple)', label: 'CODE' },
		exec: { color: 'var(--color-accent-yellow)', label: 'EXEC' },
		fail: { color: 'var(--color-accent-red)', label: 'FAIL' },
		retry: { color: 'var(--color-accent-orange)', label: 'RETRY' },
		success: { color: 'var(--color-accent-green)', label: 'DONE' }
	};

	function budgetPct(b: TaskBudget): number {
		return b.token_budget > 0 ? Math.round((b.tokens_used / b.token_budget) * 100) : 0;
	}

	function budgetColor(pct: number): string {
		if (pct > 90) return 'var(--color-accent-red)';
		if (pct > 75) return 'var(--color-accent-amber)';
		return 'var(--color-accent-cyan)';
	}
</script>

<div class="p-4 pl-6" style="font-family: var(--font-mono);">
	{#if currentTask()}
		{@const task = currentTask()!}
		{@const pct = budgetPct(task.budget)}
		{@const bColor = budgetColor(pct)}

		<!-- Header -->
		<div class="mb-3.5 flex items-center gap-2 text-[10px]" style="color: var(--color-text-faint); letter-spacing: 1px;">
			<span>TASK TIMELINE</span>
			<span class="flex-1" style="height: 1px; background: var(--color-border);"></span>
			<span style="color: var(--color-text-dim); letter-spacing: 0;">{task.description}</span>
		</div>

		<!-- Budget bar -->
		<div class="mb-4 rounded-md border p-2.5 px-3.5" style="background: var(--color-bg-activity); border-color: var(--color-border);">
			<div class="mb-2 flex items-center justify-between">
				<span class="text-[10px]" style="color: var(--color-text-dim); letter-spacing: 1px;">TASK BUDGET</span>
				<span class="text-[10px]" style="color: {pct > 90 ? 'var(--color-accent-red)' : pct > 75 ? 'var(--color-accent-amber)' : 'var(--color-text-dim)'};">
					{pct > 90 ? 'CRITICAL' : pct > 75 ? 'WARNING' : 'HEALTHY'}
				</span>
			</div>
			<div class="flex items-center gap-5">
				<div class="flex-1">
					<div class="mb-1 flex justify-between">
						<span class="text-[10px]" style="color: var(--color-text-muted);">Tokens</span>
						<span class="text-[10px]" style="color: {bColor};">{(task.budget.tokens_used / 1000).toFixed(0)}k / {(task.budget.token_budget / 1000).toFixed(0)}k ({pct}%)</span>
					</div>
					<div class="h-1.5 overflow-hidden rounded-sm" style="background: var(--color-border);">
						<div class="h-full rounded-sm transition-all duration-300" style="width: {pct}%; background: {bColor};"></div>
					</div>
				</div>
				<div class="flex gap-4">
					<div class="text-center">
						<div class="text-[14px] font-semibold" style="color: {task.budget.retries_used >= task.budget.max_retries ? 'var(--color-accent-red)' : 'var(--color-text-bright)'};">{task.budget.retries_used}/{task.budget.max_retries}</div>
						<div class="text-[8px]" style="color: var(--color-text-dim);">RETRIES</div>
					</div>
					<div class="text-center">
						<div class="text-[14px] font-semibold" style="color: var(--color-text-bright);">${task.budget.cost_used.toFixed(2)}</div>
						<div class="text-[8px]" style="color: var(--color-text-dim);">COST (${task.budget.cost_budget})</div>
					</div>
				</div>
			</div>
		</div>

		<!-- Timeline events -->
		{#each task.timeline as event, i (i)}
			{@const agent = agentInfo(event.agent)}
			{@const style = eventStyles[event.type] ?? { color: 'var(--color-text-dim)', label: '?' }}
			{@const isFail = event.type === 'fail'}
			{@const isSuccess = event.type === 'success'}
			{@const isLast = i === task.timeline.length - 1}

			<div class="relative flex gap-3 pb-0.5">
				{#if !isLast}
					<div class="absolute bottom-0 left-[13px] top-7 w-px" style="background: var(--color-border);"></div>
				{/if}

				<div
					class="z-1 flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-[10px] font-semibold"
					style="background: {agent.color}18; border: 1px solid {agent.color}25; color: {agent.color};"
				>
					{agent.name.charAt(0)}
				</div>

				<div
					class="mb-1.5 flex-1 rounded-md border p-1.5 px-3"
					style="
						background: {style.color}08;
						border-color: {isFail ? style.color + '40' : 'var(--color-border)'};
						border-left: {isFail ? '3px solid ' + style.color : isSuccess ? '3px solid ' + style.color : '1px solid var(--color-border)'};
					"
				>
					<div class="mb-0.5 flex items-center justify-between">
						<div class="flex items-center gap-1.5">
							<span class="text-[10px]" style="color: {agent.color};">{agent.name}</span>
							<span
								class="rounded-sm px-1.5 py-px text-[8px] font-semibold"
								style="color: {style.color}; background: {style.color}18; letter-spacing: 0.5px;"
							>{style.label}</span>
						</div>
						<span class="text-[9px]" style="color: var(--color-text-faint);">{event.time}</span>
					</div>
					<div class="text-[11px] leading-relaxed" style="color: {isFail ? '#f8a0a0' : isSuccess ? '#6ee7b7' : 'var(--color-text-muted)'};">
						{event.action}
					</div>
				</div>
			</div>
		{/each}

		<!-- Session debrief (if task is complete) -->
		{#if task.completed_at}
			<div class="mt-5 overflow-hidden rounded-lg border" style="background: var(--color-bg-activity); border-color: var(--color-border);">
				<div class="flex items-center justify-between border-b px-3.5 py-2.5" style="background: var(--color-bg-surface); border-color: var(--color-border);">
					<div class="flex items-center gap-2">
						<span class="text-[10px]" style="color: var(--color-text-dim); letter-spacing: 1px;">SESSION DEBRIEF</span>
						<span class="rounded-sm px-1.5 py-px text-[8px]" style="color: var(--color-accent-green); background: var(--color-accent-green)12;">COMPLETE</span>
					</div>
				</div>
				<div class="grid grid-cols-4 gap-2.5 p-3.5">
					{#each [
						{ label: 'Tokens Used', value: (task.budget.tokens_used).toLocaleString(), sub: `of ${task.budget.token_budget.toLocaleString()} budget`, icon: 'Tk' },
						{ label: 'Cost', value: `$${task.budget.cost_used.toFixed(2)}`, sub: `of $${task.budget.cost_budget} budget`, icon: '$' },
						{ label: 'Retries', value: String(task.budget.retries_used), sub: `of ${task.budget.max_retries} max`, icon: 'Rt' },
						{ label: 'Events', value: String(task.timeline.length), sub: 'timeline entries', icon: 'Ev' }
					] as metric}
						<div class="rounded-md border p-2.5" style="background: var(--color-bg-primary); border-color: var(--color-border);">
							<div class="mb-1 flex items-center justify-between">
								<span class="text-[8px]" style="color: var(--color-text-dim); letter-spacing: 0.5px;">{metric.label}</span>
								<span class="text-[8px] font-semibold" style="color: var(--color-text-faint);">{metric.icon}</span>
							</div>
							<div class="text-[18px] font-semibold" style="color: var(--color-text-bright);">{metric.value}</div>
							<div class="mt-0.5 text-[8px]" style="color: var(--color-text-dim);">{metric.sub}</div>
						</div>
					{/each}
				</div>
			</div>
		{/if}
	{:else}
		<div class="flex h-full flex-col items-center justify-center gap-3">
			<div class="text-[13px]" style="color: var(--color-text-muted);">No tasks yet</div>
			<div class="text-[11px]" style="color: var(--color-text-dim);">Use the chat panel to submit a task to the agent team.</div>
		</div>
	{/if}
</div>
