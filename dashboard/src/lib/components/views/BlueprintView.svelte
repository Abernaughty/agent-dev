<!--
	BlueprintView — displays the structured JSON blueprint for a task.

	Reads from tasksStore to find the task's blueprint data.

	Issue #38: Data Integration — PR3
-->
<script lang="ts">
	import { tasksStore } from '$lib/stores/tasks.svelte.js';
	import type { TaskSummary } from '$lib/types/api.js';

	interface Props {
		taskId: string;
	}

	let { taskId }: Props = $props();

	const task = $derived(() => tasksStore.list.find((t) => t.id === taskId) ?? null);
</script>

<div class="max-w-[750px] p-5 pl-6" style="font-family: var(--font-mono);">
	{#if task()}
		{@const t = task()!}
		<div class="mb-1.5 flex items-center gap-2.5">
			<span class="text-[16px] font-semibold" style="color: var(--color-accent-cyan);">Blueprint</span>
			<span
				class="rounded-sm px-2 py-0.5 text-[10px] uppercase"
				style="color: {t.status === 'passed' ? 'var(--color-accent-green)' : 'var(--color-accent-yellow)'};
					background: {t.status === 'passed' ? 'var(--color-accent-green)' : 'var(--color-accent-yellow)'}12;"
			>{t.status}</span>
		</div>
		<div class="mb-1 text-[14px]" style="color: var(--color-text-bright);">{t.description}</div>
		<div class="mb-5 text-[10px]" style="color: var(--color-text-dim);">Task: {t.id}</div>

		<div class="mb-2 text-[10px]" style="color: var(--color-text-dim); letter-spacing: 1px;">INSTRUCTIONS</div>
		<div
			class="mb-5 rounded-md border p-3.5"
			style="background: var(--color-bg-activity); border-color: var(--color-border); border-left: 3px solid var(--color-accent-cyan);"
		>
			<div class="text-[12px] leading-relaxed" style="color: var(--color-text-muted);">{t.description}</div>
		</div>

		<div class="mb-2 text-[10px]" style="color: var(--color-text-dim); letter-spacing: 1px;">BUDGET</div>
		<div class="mb-5 grid grid-cols-3 gap-3">
			<div class="rounded-md border p-2.5" style="background: var(--color-bg-activity); border-color: var(--color-border);">
				<div class="text-[8px]" style="color: var(--color-text-dim);">Tokens</div>
				<div class="text-[13px] font-semibold" style="color: var(--color-text-bright);">{t.budget.tokens_used.toLocaleString()} / {t.budget.token_budget.toLocaleString()}</div>
			</div>
			<div class="rounded-md border p-2.5" style="background: var(--color-bg-activity); border-color: var(--color-border);">
				<div class="text-[8px]" style="color: var(--color-text-dim);">Cost</div>
				<div class="text-[13px] font-semibold" style="color: var(--color-text-bright);">${t.budget.cost_used.toFixed(2)} / ${t.budget.cost_budget}</div>
			</div>
			<div class="rounded-md border p-2.5" style="background: var(--color-bg-activity); border-color: var(--color-border);">
				<div class="text-[8px]" style="color: var(--color-text-dim);">Retries</div>
				<div class="text-[13px] font-semibold" style="color: var(--color-text-bright);">{t.budget.retries_used} / {t.budget.max_retries}</div>
			</div>
		</div>

		<div class="mb-2 text-[10px]" style="color: var(--color-text-dim); letter-spacing: 1px;">TASK JSON</div>
		<div class="overflow-x-auto rounded-md border p-3.5" style="background: #08090e; border-color: var(--color-border);">
			<pre class="m-0 text-[11px] leading-relaxed" style="color: var(--color-text-muted);">{JSON.stringify({
				task_id: t.id,
				description: t.description,
				status: t.status,
				budget: t.budget
			}, null, 2)}</pre>
		</div>
	{:else}
		<div class="flex h-full flex-col items-center justify-center gap-3">
			<div class="text-[13px]" style="color: var(--color-text-muted);">Task not found</div>
		</div>
	{/if}
</div>
