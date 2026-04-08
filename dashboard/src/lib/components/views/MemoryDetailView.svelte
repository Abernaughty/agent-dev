<!--
	MemoryDetailView — detail view for a single memory entry.

	Shows trust assessment (computed from real confidence/sandbox/tier data),
	metadata, related files, and approve/reject buttons that call
	memoryStore mutations.

	Issue #19: L0 Approval UI — wired to real entry data
-->
<script lang="ts">
	import { memoryStore } from '$lib/stores/memory.svelte.js';
	import TrustIndicator from '$lib/components/TrustIndicator.svelte';
	import type { MemoryEntry } from '$lib/types/api.js';

	interface Props {
		entry: MemoryEntry;
	}

	let { entry }: Props = $props();

	const sandboxLabel = $derived(() => {
		if (entry.sandbox === 'locked-down') return 'Locked (trusted)';
		if (entry.sandbox === 'permissive') return 'Open egress (untrusted)';
		return entry.sandbox || 'Unknown';
	});

	const sandboxTrusted = $derived(() => entry.sandbox === 'locked-down');

	const trustScore = $derived(() => {
		const sandboxScore = sandboxTrusted() ? 30 : 10;
		const tierScore = entry.tier === 'l0-discovered' ? 10 : 20;
		const confScore = Math.round((entry.confidence ?? 0) * 0.4);
		return Math.min(100, sandboxScore + tierScore + confScore);
	});

	const trustLevel = $derived(() => {
		const s = trustScore();
		if (s >= 75) return { label: 'HIGH', color: 'var(--color-accent-green)' };
		if (s >= 50) return { label: 'MEDIUM', color: 'var(--color-accent-amber)' };
		return { label: 'LOW', color: 'var(--color-accent-red)' };
	});

	const tierColor = $derived(() =>
		entry.tier.includes('l0') ? 'var(--color-accent-amber)' : 'var(--color-accent-purple)'
	);

	let actionInProgress = $state(false);

	async function handleApprove() {
		actionInProgress = true;
		await memoryStore.approve(entry.id);
		actionInProgress = false;
	}

	async function handleReject() {
		actionInProgress = true;
		await memoryStore.reject(entry.id);
		actionInProgress = false;
	}
</script>

<div class="max-w-[700px] p-5 pl-6" style="font-family: var(--font-mono);">
	<div class="mb-5 flex items-center gap-2.5">
		<span class="rounded-md px-2.5 py-1 text-[11px] font-semibold" style="color: {tierColor()}; background: {tierColor()}15;">{entry.tier}</span>
		<span class="text-[11px]" style="color: var(--color-text-dim);">from {entry.source_agent}</span>
		<span class="text-[11px]" style="color: var(--color-text-faint);">{entry.module}</span>
	</div>

	<!-- Trust Assessment -->
	<div class="mb-5 rounded-md border p-3" style="background: {trustLevel().color}08; border-color: {trustLevel().color}20;">
		<div class="mb-2.5 flex items-center justify-between">
			<span class="text-[10px]" style="color: var(--color-text-dim); letter-spacing: 1px;">TRUST ASSESSMENT</span>
			<div class="flex items-center gap-2">
				<div class="h-1.5 w-[60px] overflow-hidden rounded-sm" style="background: var(--color-border);">
					<div class="h-full rounded-sm" style="width: {trustScore()}%; background: {trustLevel().color};"></div>
				</div>
				<span class="text-[11px] font-semibold" style="color: {trustLevel().color};">{trustLevel().label} ({trustScore()})</span>
			</div>
		</div>
		<div class="flex gap-3">
			{#each [
				{ label: 'Sandbox', value: sandboxLabel(), good: sandboxTrusted() },
				{ label: 'Confidence', value: `${entry.confidence ?? 0}%`, good: (entry.confidence ?? 0) >= 85 },
				{ label: 'Tier', value: entry.tier, good: entry.tier === 'l1' }
			] as factor}
				<div class="flex-1 rounded-md p-1.5 px-2" style="background: var(--color-bg-activity)60;">
					<div class="text-[8px]" style="color: var(--color-text-dim);">{factor.label}</div>
					<div class="text-[10px]" style="color: {factor.good ? 'var(--color-accent-green)' : 'var(--color-accent-amber)'};">{factor.value}</div>
				</div>
			{/each}
		</div>
	</div>

	<!-- Content -->
	<div class="mb-5 rounded-md border p-4" style="background: var(--color-bg-activity); border-color: var(--color-border); border-left: 3px solid {tierColor()};">
		<div class="text-[13px] leading-relaxed" style="color: var(--color-text-bright);">"{entry.content}"</div>
	</div>

	<!-- Context (issue #110) -->
	{#if entry.source_step || entry.source_output_ref}
		<div class="mb-5">
			<div class="mb-2 text-[10px]" style="color: var(--color-text-dim); letter-spacing: 1px;">CONTEXT</div>
			<div class="rounded-md border p-3" style="background: var(--color-bg-activity); border-color: var(--color-border);">
				{#if entry.source_step}
					<div class="mb-1.5 flex items-center gap-2">
						<span class="text-[9px]" style="color: var(--color-text-dim);">Step</span>
						<span class="rounded px-1.5 py-0.5 text-[11px]" style="color: var(--color-accent-cyan); background: var(--color-accent-cyan)10;">{entry.source_step}</span>
					</div>
				{/if}
				{#if entry.source_output_ref}
					<div class="text-[11px] leading-relaxed" style="color: var(--color-text-muted);">{entry.source_output_ref}</div>
				{/if}
			</div>
		</div>
	{/if}

	<!-- Metadata Grid -->
	<div class="mb-5 grid grid-cols-2 gap-3">
		{#each [
			{ label: 'Confidence', value: `${entry.confidence ?? 0}%`, color: (entry.confidence ?? 0) >= 85 ? 'var(--color-accent-green)' : 'var(--color-accent-amber)' },
			{ label: 'Sandbox Origin', value: sandboxLabel(), color: 'var(--color-accent-cyan)' },
			{ label: 'Source Agent', value: entry.source_agent, color: 'var(--color-accent-cyan)' },
			{ label: 'Expires', value: entry.hours_remaining ? `${entry.hours_remaining.toFixed(0)}h remaining` : 'Never (L1)', color: entry.hours_remaining ? 'var(--color-accent-amber)' : 'var(--color-text-dim)' },
			{ label: 'Module', value: entry.module, color: 'var(--color-text-muted)' },
			{ label: 'Verified', value: entry.verified ? 'Yes' : 'No', color: entry.verified ? 'var(--color-accent-green)' : 'var(--color-accent-amber)' }
		] as item}
			<div class="rounded-md border p-2.5" style="background: var(--color-bg-activity); border-color: var(--color-border);">
				<div class="mb-1 text-[9px]" style="color: var(--color-text-dim); letter-spacing: 0.5px;">{item.label}</div>
				<div class="text-[11px] leading-snug" style="color: {item.color};">{item.value}</div>
			</div>
		{/each}
	</div>

	<!-- Related Files -->
	{#if entry.related_files && entry.related_files.length > 0}
		<div class="mb-5">
			<div class="mb-2 text-[10px]" style="color: var(--color-text-dim); letter-spacing: 1px;">RELATED FILES</div>
			<div class="flex flex-col gap-1">
				{#each entry.related_files as file}
					<div class="rounded px-2 py-1 text-[11px]" style="color: var(--color-accent-cyan); background: var(--color-accent-cyan)08;">{file}</div>
				{/each}
			</div>
		</div>
	{/if}

	<!-- Actions -->
	{#if entry.status === 'pending'}
		<div class="flex gap-2.5">
			<button onclick={handleApprove} disabled={actionInProgress} class="cursor-pointer rounded-md border px-6 py-2 text-[12px] font-medium transition-opacity disabled:cursor-not-allowed disabled:opacity-50" style="background: var(--color-accent-green)20; border-color: var(--color-accent-green)40; color: var(--color-accent-green);">
				{actionInProgress ? 'Saving...' : 'Approve \u2014 Promote to L0'}
			</button>
			<button onclick={handleReject} disabled={actionInProgress} class="cursor-pointer rounded-md border px-6 py-2 text-[12px] transition-opacity disabled:cursor-not-allowed disabled:opacity-50" style="background: var(--color-accent-red)18; border-color: var(--color-accent-red)30; color: var(--color-accent-red);">
				Reject
			</button>
		</div>
	{:else}
		<div class="text-[12px] uppercase" style="color: {entry.status === 'approved' ? 'var(--color-accent-green)' : 'var(--color-accent-red)'}; letter-spacing: 1px;">{entry.status}</div>
	{/if}
</div>
