<!--
	PRDetailView — full PR detail with real diffs, reviews, comments,
	check status, and task backlink.

	Fetches detail, files, reviews, and comments on-demand via prsStore.

	Issue #38: Initial stub
	Issue #109: Full lifecycle — real diffs, reviews, comments, cross-nav
-->
<script lang="ts">
	import { untrack } from 'svelte';
	import { Marked } from 'marked';
	import { prsStore } from '$lib/stores/prs.svelte.js';
	import { getDashboardContext } from '$lib/stores/dashboard.svelte.js';
	import type { PullRequest, PRFileChange, PRReview, PRComment } from '$lib/types/api.js';

	interface Props {
		prNumber: number;
	}

	let { prNumber }: Props = $props();

	const dash = getDashboardContext();

	// Reactive data from store
	const pr = $derived.by(() => {
		// First check detail cache, then fall back to list entry
		const detail = prsStore.getDetail(prNumber);
		if (detail) return detail;
		return prsStore.list.find((p) => p.number === prNumber) ?? null;
	});
	const files = $derived.by(() => prsStore.getFiles(prNumber));
	const reviews = $derived.by(() => prsStore.getReviews(prNumber));
	const comments = $derived.by(() => prsStore.getComments(prNumber));
	const isLoading = $derived(prsStore.isDetailLoading(prNumber));
	const fetchError = $derived(prsStore.getDetailError(prNumber));

	// Fetch detail + files + reviews when prNumber changes
	$effect(() => {
		const num = prNumber;
		if (num) {
			untrack(() => {
				prsStore.fetchDetail(num);
				prsStore.fetchFiles(num);
				prsStore.fetchReviews(num);
				prsStore.fetchComments(num);
			});
		}
	});

	let expandedFile = $state<string | null>(null);
	let showReviews = $state(true);
	let showComments = $state(false);
	let showChecks = $state(false);

	// Default to first file expanded
	$effect(() => {
		const f = files;
		if (f && f.length > 0) {
			untrack(() => {
				expandedFile = f[0].name;
			});
		}
	});

	// Markdown rendering for review bodies
	const marked = new Marked({ breaks: true, gfm: true });

	function renderMarkdown(text: string): string {
		try {
			const result = marked.parse(text);
			if (typeof result === 'string') return result;
			return text;
		} catch {
			return text;
		}
	}

	/** Detect CodeRabbit bot reviews. */
	function isCodeRabbit(review: PRReview): boolean {
		return review.is_bot && review.author.includes('coderabbit');
	}

	/** Map review state to display label + color. */
	function reviewStateInfo(state: string): { label: string; color: string } {
		switch (state.toLowerCase()) {
			case 'approved':
				return { label: 'APPROVED', color: 'var(--color-accent-green)' };
			case 'changes_requested':
				return { label: 'CHANGES', color: 'var(--color-accent-red)' };
			case 'commented':
				return { label: 'COMMENTED', color: 'var(--color-accent-amber)' };
			case 'dismissed':
				return { label: 'DISMISSED', color: 'var(--color-text-dim)' };
			default:
				return { label: state.toUpperCase(), color: 'var(--color-text-dim)' };
		}
	}

	/** Try to extract a task_id from PR body (publish_code_node tags it). */
	function extractTaskId(body: string | null | undefined): string | null {
		if (!body) return null;
		const match = body.match(/task[_-]id[:\s]+([a-zA-Z0-9_-]+)/i);
		return match ? match[1] : null;
	}

	function navigateToTask(taskId: string) {
		dash.handlePanelSwitch('agents');
		dash.handleSelect(`task-${taskId}`);
	}

	function refreshPR() {
		prsStore.refreshPR(prNumber);
	}

	/** Check run conclusion color. */
	function checkColor(conclusion: string | null): string {
		switch (conclusion) {
			case 'success': return 'var(--color-accent-green)';
			case 'failure': return 'var(--color-accent-red)';
			case 'neutral': return 'var(--color-text-dim)';
			case 'cancelled': return 'var(--color-text-dim)';
			case 'skipped': return 'var(--color-text-dim)';
			case 'timed_out': return 'var(--color-accent-orange)';
			case 'action_required': return 'var(--color-accent-amber)';
			default: return 'var(--color-accent-yellow)';
		}
	}

	const statusColors: Record<string, string> = {
		open: 'var(--color-accent-yellow)',
		review: 'var(--color-accent-yellow)',
		merged: 'var(--color-accent-green)',
		closed: 'var(--color-accent-red)',
		draft: 'var(--color-text-dim)'
	};
</script>

<div class="max-w-[800px] p-5 pl-6" style="font-family: var(--font-mono);">
	{#if pr}
		{@const sColor = statusColors[pr.status] || 'var(--color-text-dim)'}
		{@const taskId = extractTaskId(pr.summary)}

		<!-- Header -->
		<div class="mb-1.5 flex items-center gap-2.5">
			<span class="text-[16px] font-semibold" style="color: var(--color-accent-cyan);">#{pr.number}</span>
			<span class="rounded-sm px-2 py-0.5 text-[10px] font-semibold uppercase" style="color: {sColor}; background: {sColor}15;">{pr.status}</span>
			{#if pr.draft}
				<span class="rounded-sm px-2 py-0.5 text-[10px] uppercase" style="color: var(--color-text-dim); background: var(--color-text-dim)15;">DRAFT</span>
			{/if}
		</div>
		<div class="mb-1 text-[14px]" style="color: var(--color-text-bright);">{pr.title}</div>
		<div class="mb-4 flex items-center gap-3 text-[11px]" style="color: var(--color-text-dim);">
			<span>{pr.branch} \u2192 {pr.base}</span>
			<span>by {pr.author}</span>
			{#if taskId}
				<span style="color: var(--color-text-faint);">|</span>
				<button
					onclick={() => navigateToTask(taskId)}
					class="cursor-pointer underline"
					style="color: var(--color-accent-cyan); background: none; border: none; font-family: var(--font-mono); font-size: 11px;"
				>Task {taskId}</button>
			{/if}
			<span class="flex-1"></span>
			<button
				onclick={refreshPR}
				class="flex cursor-pointer items-center gap-1.5 rounded border px-2.5 py-1 text-[11px] transition-opacity hover:opacity-100"
				style="background: var(--color-bg-surface); border-color: var(--color-border-secondary, var(--color-border)); color: var(--color-text-dim);"
			>\u21BB Refresh</button>
		</div>

		{#if pr.summary}
			<div class="mb-5 text-[12px] leading-relaxed" style="color: var(--color-text-muted); white-space: pre-wrap;">{pr.summary.length > 500 ? pr.summary.slice(0, 500) + '...' : pr.summary}</div>
		{/if}

		{#if isLoading}
			<div class="mb-4 rounded-md border px-3 py-2 text-[11px]" style="background: var(--color-bg-activity); border-color: var(--color-border); color: var(--color-text-dim);">Loading PR details...</div>
		{/if}

		{#if fetchError && !isLoading}
			<div class="mb-4 rounded-md border px-3 py-2 text-[11px]" style="background: var(--color-accent-red)08; border-color: var(--color-accent-red)20; color: var(--color-accent-red);">{fetchError}</div>
		{/if}

		<!-- Stats bar -->
		<div class="mb-5 flex gap-4 text-[11px]">
			<span style="color: var(--color-text-dim);">{pr.file_count} files</span>
			<span style="color: var(--color-accent-green);">+{pr.additions}</span>
			<span style="color: var(--color-accent-red);">-{pr.deletions}</span>
			{#if pr.tests && pr.tests.total > 0}
				<span style="color: {pr.tests.failed === 0 ? 'var(--color-accent-green)' : 'var(--color-accent-red)'};">Tests: {pr.tests.passed}/{pr.tests.total}</span>
			{/if}
			{#if pr.mergeable !== null && pr.mergeable !== undefined}
				<span style="color: {pr.mergeable ? 'var(--color-accent-green)' : 'var(--color-accent-red)'};">Mergeable: {pr.mergeable ? 'Yes' : 'No'}</span>
			{/if}
		</div>

		<!-- Reviews section -->
		{#if reviews && reviews.length > 0}
			<button
				onclick={() => showReviews = !showReviews}
				class="mb-3 flex w-full cursor-pointer items-center justify-between rounded-md border px-3.5 py-2 text-left"
				style="background: var(--color-bg-surface); border-color: var(--color-border-secondary, var(--color-border)); font-family: var(--font-mono);"
			>
				<div class="flex items-center gap-2">
					<span class="text-[11px]" style="color: var(--color-text-faint);">{showReviews ? '\u25BC' : '\u25B6'}</span>
					<span class="text-[12px] font-medium" style="color: var(--color-text-bright);">Reviews</span>
				</div>
				<span class="text-[11px]" style="color: var(--color-text-dim);">{reviews.length} review{reviews.length !== 1 ? 's' : ''}</span>
			</button>

			{#if showReviews}
				<div class="mb-5 flex flex-col gap-2">
					{#each reviews as review}
						{@const stateInfo = reviewStateInfo(review.state)}
						{@const isCR = isCodeRabbit(review)}
						<div
							class="rounded-md border p-3"
							style="background: var(--color-bg-activity); border-color: var(--color-border); border-left: 2px solid {stateInfo.color};"
						>
							<div class="mb-2 flex items-center gap-2">
								<span class="text-[11px] font-medium" style="color: var(--color-text-bright);">{review.author}</span>
								{#if isCR}
									<span class="rounded-sm px-1.5 py-px text-[8px] font-semibold uppercase" style="color: var(--color-accent-cyan); background: var(--color-accent-cyan)15;">CodeRabbit</span>
								{:else if review.is_bot}
									<span class="rounded-sm px-1.5 py-px text-[8px] font-semibold uppercase" style="color: var(--color-text-dim); background: var(--color-text-dim)15;">BOT</span>
								{/if}
								<span class="rounded-sm px-1.5 py-px text-[8px] font-semibold" style="color: {stateInfo.color}; background: {stateInfo.color}15;">{stateInfo.label}</span>
								{#if review.submitted_at}
									<span class="text-[10px]" style="color: var(--color-text-faint);">{review.submitted_at.split('T')[0]}</span>
								{/if}
							</div>
							{#if review.body}
								<div class="review-content max-h-[200px] overflow-y-auto text-[12px] leading-relaxed" style="color: var(--color-text-muted);">
									{@html renderMarkdown(review.body.length > 2000 ? review.body.slice(0, 2000) + '\n\n*[Truncated — view full review on GitHub]*' : review.body)}
								</div>
							{/if}
						</div>
					{/each}
				</div>
			{/if}
		{/if}

		<!-- Check Runs -->
		{#if pr.check_status && pr.check_status.length > 0}
			<button
				onclick={() => showChecks = !showChecks}
				class="mb-3 flex w-full cursor-pointer items-center justify-between rounded-md border px-3.5 py-2 text-left"
				style="background: var(--color-bg-surface); border-color: var(--color-border-secondary, var(--color-border)); font-family: var(--font-mono);"
			>
				<div class="flex items-center gap-2">
					<span class="text-[11px]" style="color: var(--color-text-faint);">{showChecks ? '\u25BC' : '\u25B6'}</span>
					<span class="text-[12px] font-medium" style="color: var(--color-text-bright);">Check runs</span>
				</div>
				<span class="text-[11px]" style="color: var(--color-text-dim);">{pr.check_status.length} check{pr.check_status.length !== 1 ? 's' : ''}</span>
			</button>

			{#if showChecks}
				<div class="mb-5 flex flex-col gap-1">
					{#each pr.check_status as check}
						<div class="flex items-center gap-2 rounded-md border px-3 py-1.5" style="background: var(--color-bg-activity); border-color: var(--color-border);">
							<span class="text-[11px]" style="color: {checkColor(check.conclusion)};">{check.conclusion === 'success' ? '\u2713' : check.conclusion === 'failure' ? '\u2717' : '\u25CB'}</span>
							<span class="text-[11px]" style="color: var(--color-text-muted);">{check.name}</span>
							<span class="flex-1"></span>
							<span class="text-[10px]" style="color: {checkColor(check.conclusion)};">{check.conclusion || check.status}</span>
						</div>
					{/each}
				</div>
			{/if}
		{/if}

		<!-- Comments -->
		{#if comments && comments.length > 0}
			<button
				onclick={() => showComments = !showComments}
				class="mb-3 flex w-full cursor-pointer items-center justify-between rounded-md border px-3.5 py-2 text-left"
				style="background: var(--color-bg-surface); border-color: var(--color-border-secondary, var(--color-border)); font-family: var(--font-mono);"
			>
				<div class="flex items-center gap-2">
					<span class="text-[11px]" style="color: var(--color-text-faint);">{showComments ? '\u25BC' : '\u25B6'}</span>
					<span class="text-[12px] font-medium" style="color: var(--color-text-bright);">Comments</span>
				</div>
				<span class="text-[11px]" style="color: var(--color-text-dim);">{comments.length} comment{comments.length !== 1 ? 's' : ''}</span>
			</button>

			{#if showComments}
				<div class="mb-5 flex flex-col gap-2">
					{#each comments as comment}
						<div class="rounded-md border p-3" style="background: var(--color-bg-activity); border-color: var(--color-border);">
							<div class="mb-1.5 flex items-center gap-2">
								<span class="text-[11px] font-medium" style="color: var(--color-text-bright);">{comment.author}</span>
								{#if comment.is_bot}
									<span class="rounded-sm px-1.5 py-px text-[8px] font-semibold uppercase" style="color: var(--color-text-dim); background: var(--color-text-dim)15;">BOT</span>
								{/if}
								{#if comment.path}
									<span class="text-[10px]" style="color: var(--color-accent-cyan);">{comment.path}{comment.line ? `:${comment.line}` : ''}</span>
								{/if}
								{#if comment.created_at}
									<span class="text-[10px]" style="color: var(--color-text-faint);">{comment.created_at.split('T')[0]}</span>
								{/if}
							</div>
							<div class="review-content text-[12px] leading-relaxed" style="color: var(--color-text-muted);">
								{@html renderMarkdown(comment.body.length > 1000 ? comment.body.slice(0, 1000) + '\n\n*[Truncated]*' : comment.body)}
							</div>
						</div>
					{/each}
				</div>
			{/if}
		{/if}

		<!-- Changed Files -->
		{#if files}
			<div class="mb-2 text-[12px] font-medium" style="color: var(--color-text-bright);">Changed files</div>
			{#each files as file}
				<div class="mb-1">
					<button
						onclick={() => (expandedFile = expandedFile === file.name ? null : file.name)}
						class="flex w-full items-center justify-between rounded-md border px-2.5 py-1.5 text-left"
						style="background: {expandedFile === file.name ? 'var(--color-bg-surface)' : 'var(--color-bg-activity)'}; border-color: var(--color-border); {expandedFile === file.name ? 'border-radius: 4px 4px 0 0;' : ''}"
					>
						<div class="flex items-center gap-1.5">
							<span class="text-[11px]" style="color: var(--color-text-muted);">{file.name}</span>
							<span class="text-[8px] uppercase" style="color: {file.status === 'added' ? 'var(--color-accent-cyan)' : file.status === 'modified' ? 'var(--color-accent-amber)' : file.status === 'removed' ? 'var(--color-accent-red)' : 'var(--color-text-dim)'}; letter-spacing: 0.5px;">{file.status}</span>
						</div>
						<div class="flex gap-1.5 text-[10px]">
							<span style="color: var(--color-accent-green);">+{file.additions}</span>
							<span style="color: var(--color-accent-red);">-{file.deletions}</span>
						</div>
					</button>
					{#if expandedFile === file.name}
						<div class="overflow-x-auto rounded-b-md border border-t-0 px-3.5 py-2.5" style="background: #08090e; border-color: var(--color-border);">
							{#if file.patch}
								<pre style="font-family: var(--font-mono); font-size: 11px; line-height: 1.7; margin: 0; white-space: pre-wrap;">{#each file.patch.split('\n') as line}<span style="display: block; color: {line.startsWith('+') ? 'var(--color-accent-green)' : line.startsWith('-') ? 'var(--color-accent-red)' : line.startsWith('@@') ? 'var(--color-accent-cyan)' : 'var(--color-text-dim)'}; background: {line.startsWith('+') ? 'var(--color-accent-green)08' : line.startsWith('-') ? 'var(--color-accent-red)08' : 'transparent'}; padding: 0 4px; border-radius: 2px;">{line}</span>{/each}</pre>
							{:else}
								<div class="text-[11px] leading-relaxed" style="color: var(--color-text-dim);">No diff available (binary file or too large).</div>
							{/if}
						</div>
					{/if}
				</div>
			{/each}
		{:else if !isLoading}
			<!-- Fallback: files from list-level PR data (no patches) -->
			{#if pr.files && pr.files.length > 0}
				<div class="mb-2 text-[12px] font-medium" style="color: var(--color-text-bright);">Changed files</div>
				{#each pr.files as file}
					<div class="mb-1 flex items-center justify-between rounded-md border px-2.5 py-1.5" style="background: var(--color-bg-activity); border-color: var(--color-border);">
						<span class="text-[11px]" style="color: var(--color-text-muted);">{file.name}</span>
						<div class="flex gap-1.5 text-[10px]">
							<span style="color: var(--color-accent-green);">+{file.additions}</span>
							<span style="color: var(--color-accent-red);">-{file.deletions}</span>
						</div>
					</div>
				{/each}
			{/if}
		{/if}

		<!-- GitHub link -->
		<div class="mt-5 text-[11px]" style="color: var(--color-text-dim);">
			<a
				href="https://github.com/{pr.author === 'unknown' ? 'Abernaughty' : pr.author}/agent-dev/pull/{pr.number}"
				target="_blank"
				rel="noopener"
				class="underline"
				style="color: var(--color-accent-cyan);"
			>View on GitHub \u2197</a>
		</div>
	{:else}
		<div class="text-[13px]" style="color: var(--color-text-muted);">PR not found.</div>
	{/if}
</div>

<style>
	.review-content :global(h1),
	.review-content :global(h2),
	.review-content :global(h3),
	.review-content :global(h4) {
		color: var(--color-text-bright);
		font-weight: 500;
		margin-top: 0.8em;
		margin-bottom: 0.3em;
	}
	.review-content :global(h1) { font-size: 14px; }
	.review-content :global(h2) { font-size: 13px; }
	.review-content :global(h3) { font-size: 12px; }

	.review-content :global(p) {
		margin-bottom: 0.5em;
		line-height: 1.6;
	}

	.review-content :global(code) {
		background: var(--color-bg-surface);
		color: var(--color-accent-cyan);
		padding: 1px 4px;
		border-radius: 3px;
		font-size: 11px;
	}

	.review-content :global(pre) {
		background: var(--color-bg-primary);
		border: 1px solid var(--color-border);
		border-radius: 4px;
		padding: 8px 10px;
		margin: 0.5em 0;
		overflow-x: auto;
		font-size: 11px;
	}

	.review-content :global(pre code) {
		background: none;
		padding: 0;
		color: var(--color-text-muted);
	}

	.review-content :global(ul),
	.review-content :global(ol) {
		padding-left: 1.4em;
		margin-bottom: 0.5em;
	}

	.review-content :global(li) {
		margin-bottom: 0.2em;
		line-height: 1.6;
	}

	.review-content :global(blockquote) {
		border-left: 2px solid var(--color-accent-cyan);
		padding-left: 10px;
		margin: 0.5em 0;
		color: var(--color-text-dim);
	}

	.review-content :global(a) {
		color: var(--color-accent-cyan);
		text-decoration: underline;
	}

	.review-content :global(hr) {
		border: none;
		border-top: 1px solid var(--color-border);
		margin: 0.8em 0;
	}

	.review-content :global(strong) {
		color: var(--color-text-bright);
		font-weight: 500;
	}

	.review-content :global(:first-child) {
		margin-top: 0;
	}

	.review-content :global(:last-child) {
		margin-bottom: 0;
	}
</style>
