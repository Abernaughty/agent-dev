<script lang="ts">
	const menuItems = ['File', 'Edit', 'View'];
</script>

<div
	class="relative flex h-[30px] shrink-0 items-center border-b px-3 select-none"
	style="background: var(--color-bg-activity); border-color: var(--color-border); font-family: var(--font-mono);"
>
	<div class="flex items-center gap-3">
		{#each menuItems as item}
			<span
				class="text-[10px] cursor-default"
				style="color: var(--color-text-dim);"
			>
				{item}
			</span>
		{/each}
	</div>

	<span
		class="absolute left-1/2 -translate-x-1/2 text-[11px] font-semibold tracking-wide"
		style="color: var(--color-accent-cyan);"
	>
		dev-suite
	</span>
</div>
