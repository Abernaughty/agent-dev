<!--
	PRDetailView — detail view for a single pull request.

	Shows PR metadata, file changes with expandable diffs, and test results.

	Issue #38: Data Integration — PR3
-->
<script lang="ts">
	import type { PullRequest } from '$lib/types/api.js';

	interface Props {
		pr: PullRequest;
	}

	let { pr }: Props = $props();

	let expandedFile = $state<string | null>(null);

	// Default to first file expanded when pr changes
	$effect(() => {
		expandedFile = pr.files[0]?.name ?? null;
	});
</script>

<div class="p-5 pl-6" style="font-family: var(--font-mono);">
	<div class="mb-1.5 flex items-center gap-2.5">
		<span class="text-[16px] font-semibold" style="color: var(--color-accent-cyan);">{pr.id}</span>
		<span class="rounded-sm px-2 py-0.5 text-[10px] uppercase" style="color: {pr.status === 'merged' ? 'var(--color-accent-green)' : 'var(--color-accent-yellow)'}; background: {pr.status === 'merged' ? 'var(--color-accent-green)' : 'var(--color-accent-yellow)'}12;">{pr.status}</span>
	</div>
	<div class="mb-1 text-[14px]" style="color: var(--color-text-bright);">{pr.title}</div>
	<div class="mb-4 text-[10px]" style="color: var(--color-text-dim);">{pr.branch} → {pr.base} | by {pr.author}</div>
	<div class="mb-5 text-[11px] leading-relaxed" style="color: var(--color-text-muted);">{pr.summary}</div>

	<div class="mb-5 flex gap-4 text-[11px]">
		<span style="color: var(--color-text-dim);">{pr.file_count} files</span>
		<span style="color: var(--color-accent-green);">+{pr.additions}</span>
		<span style="color: var(--color-accent-red);">-{pr.deletions}</span>
		<span style="color: {pr.tests.failed === 0 ? 'var(--color-accent-green)' : 'var(--color-accent-red)'};">Tests: {pr.tests.passed}/{pr.tests.total}</span>
	</div>

	<div class="mb-2 text-[10px]" style="color: var(--color-text-dim); letter-spacing: 1px;">CHANGED FILES</div>
	{#each pr.files as file}
		<div class="mb-1">
			<button
				onclick={() => (expandedFile = expandedFile === file.name ? null : file.name)}
				class="flex w-full items-center justify-between rounded-md border px-2.5 py-1.5 text-left"
				style="background: {expandedFile === file.name ? 'var(--color-bg-surface)' : 'var(--color-bg-activity)'}; border-color: var(--color-border); {expandedFile === file.name ? 'border-radius: 4px 4px 0 0;' : ''}"
			>
				<div class="flex items-center gap-1.5">
					<span class="text-[11px]" style="color: var(--color-text-muted);">{file.name}</span>
					<span class="text-[8px] uppercase" style="color: {file.status === 'added' ? 'var(--color-accent-cyan)' : file.status === 'modified' ? 'var(--color-accent-amber)' : 'var(--color-text-dim)'}; letter-spacing: 0.5px;">{file.status}</span>
				</div>
				<div class="flex gap-1.5 text-[10px]">
					<span style="color: var(--color-accent-green);">+{file.additions}</span>
					<span style="color: var(--color-accent-red);">-{file.deletions}</span>
				</div>
			</button>
			{#if expandedFile === file.name}
				<div class="overflow-x-auto rounded-b-md border border-t-0 px-3.5 py-2.5" style="background: #08090e; border-color: var(--color-border);">
					<div class="text-[11px] leading-relaxed" style="color: var(--color-text-dim);">Diff content available when connected to live backend.</div>
				</div>
			{/if}
		</div>
	{/each}
</div>
