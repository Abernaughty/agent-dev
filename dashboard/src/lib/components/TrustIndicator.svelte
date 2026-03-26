<!--
	TrustIndicator — composite trust score panel for memory entries.

	Calculates a weighted score from:
	  - Sandbox origin (locked-down = 30, open = 10)
	  - Confidence score (scaled to 40% weight)
	  - Memory tier (L1 = 20, L0-Discovered = 10)

	Used inside MemoryDetailView to help humans assess
	whether to approve agent-discovered constraints.

	Relates to #17, #19
-->
<script lang="ts">
	interface Props {
		confidence: number;
		sandbox: string;
		tier: string;
	}

	let { confidence, sandbox, tier }: Props = $props();

	const sandboxScore = $derived(sandbox === 'locked-down' ? 30 : 10);
	const tierScore = $derived(tier === 'l0-discovered' ? 10 : 20);
	const confScore = $derived(Math.round(confidence * 0.4));
	const total = $derived(Math.min(100, sandboxScore + tierScore + confScore));

	const level = $derived(() => {
		if (total >= 75) return { label: 'HIGH', color: 'var(--color-accent-green)' };
		if (total >= 50) return { label: 'MEDIUM', color: 'var(--color-accent-amber)' };
		return { label: 'LOW', color: 'var(--color-accent-red)' };
	});

	const factors = $derived([
		{
			label: 'Sandbox',
			value: sandbox === 'locked-down' ? 'Locked (trusted)' : 'Open egress (untrusted)',
			good: sandbox === 'locked-down'
		},
		{
			label: 'Confidence',
			value: `${confidence}%`,
			good: confidence >= 85
		},
		{
			label: 'Tier',
			value: tier,
			good: tier === 'l1'
		}
	]);
</script>

<div
	class="mb-5 rounded-md border p-3"
	style="background: {level().color}08; border-color: {level().color}20;"
>
	<div class="mb-2.5 flex items-center justify-between" style="font-family: var(--font-mono);">
		<span class="text-[10px]" style="color: var(--color-text-dim); letter-spacing: 1px;"
			>TRUST ASSESSMENT</span
		>
		<div class="flex items-center gap-2">
			<div
				class="h-1.5 w-[60px] overflow-hidden rounded-sm"
				style="background: var(--color-border);"
			>
				<div
					class="h-full rounded-sm transition-all duration-300"
					style="width: {total}%; background: {level().color};"
				></div>
			</div>
			<span class="text-[11px] font-semibold" style="color: {level().color};"
				>{level().label} ({total})</span
			>
		</div>
	</div>
	<div class="flex gap-3">
		{#each factors as factor}
			<div class="flex-1 rounded-md p-1.5 px-2" style="background: var(--color-bg-activity)60;">
				<div class="text-[8px]" style="color: var(--color-text-dim);">{factor.label}</div>
				<div
					class="text-[10px]"
					style="color: {factor.good
						? 'var(--color-accent-green)'
						: 'var(--color-accent-amber)'};"
				>
					{factor.value}
				</div>
			</div>
		{/each}
	</div>
</div>
