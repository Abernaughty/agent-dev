<script lang="ts">
	const menuItems = ['File', 'Edit', 'View'];
</script>

<div
	class="flex h-[30px] shrink-0 items-center border-b px-3 gap-4 select-none"
	style="background: var(--color-bg-activity); border-color: var(--color-border); font-family: var(--font-mono);"
>
	<span
		class="text-[11px] font-semibold tracking-wide"
		style="color: var(--color-accent-cyan);"
	>
		dev-suite
	</span>

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

	<div class="flex-1"></div>
</div>
