<script lang="ts">
	type PanelId = 'agents' | 'memory' | 'prs' | 'chat';

	interface Props {
		activePanel: PanelId | null;
		onSelect: (panel: PanelId | null) => void;
		pendingMemory?: number;
		pendingPR?: number;
	}

	let { activePanel, onSelect, pendingMemory = 0, pendingPR = 0 }: Props = $props();

	const items: { id: PanelId; label: string; badge: number }[] = $derived([
		{ id: 'chat', label: 'Ch', badge: 0 },
		{ id: 'agents', label: 'Ag', badge: 0 },
		{ id: 'memory', label: 'Mm', badge: pendingMemory },
		{ id: 'prs', label: 'PR', badge: pendingPR }
	]);
</script>

<div
	class="flex h-full w-12 shrink-0 flex-col items-center border-r pt-2 gap-0.5"
	style="background: var(--color-bg-activity); border-color: var(--color-border);"
>
	{#each items as item (item.id)}
		{@const isActive = activePanel === item.id}
		<button
			onclick={() => onSelect(isActive ? null : item.id)}
			class="relative flex h-10 w-10 items-center justify-center border-l-2 text-[10px] font-semibold tracking-tight transition-opacity"
			style="
				background: transparent;
				border-color: {isActive ? 'var(--color-accent-cyan)' : 'transparent'};
				color: {isActive ? 'var(--color-text-bright)' : 'var(--color-text-dim)'};
				opacity: {isActive ? 1 : 0.5};
				font-family: var(--font-mono);
			"
		>
			{item.label}
			{#if item.badge > 0}
				<span
					class="absolute top-1 right-0.5 flex h-3.5 min-w-3.5 items-center justify-center rounded-full text-[9px] font-bold"
					style="background: var(--color-accent-amber); color: var(--color-bg-activity); font-family: var(--font-mono);"
				>
					{item.badge}
				</span>
			{/if}
		</button>
	{/each}
	<div class="flex-1"></div>
</div>
