<!--
	ConnectionBanner — displays at top of content area when backend is unreachable.

	Driven by the `connection` store:
	- connected    → hidden
	- reconnecting → amber "Reconnecting..." with attempt count
	- disconnected → red "Backend unreachable"

	Issue #38: Data Integration — Bootstrap
-->
<script lang="ts">
	import { connection } from '$lib/stores/connection.svelte.js';
</script>

{#if connection.status === 'reconnecting'}
	<div
		class="flex items-center justify-center gap-2 px-4 py-1.5 text-[11px]"
		style="
			background: rgba(245, 158, 11, 0.12);
			border-bottom: 1px solid rgba(245, 158, 11, 0.25);
			color: var(--color-accent-amber);
			font-family: var(--font-mono);
		"
	>
		<span
			class="inline-block h-1.5 w-1.5 rounded-full"
			style="background: var(--color-accent-amber); animation: pulse 1.5s ease-in-out infinite;"
		></span>
		Reconnecting to backend... (attempt {connection.reconnectAttempt})
	</div>
{:else if connection.status === 'disconnected'}
	<div
		class="flex items-center justify-center gap-2 px-4 py-1.5 text-[11px]"
		style="
			background: rgba(239, 68, 68, 0.12);
			border-bottom: 1px solid rgba(239, 68, 68, 0.25);
			color: var(--color-accent-red);
			font-family: var(--font-mono);
		"
	>
		<span
			class="inline-block h-1.5 w-1.5 rounded-full"
			style="background: var(--color-accent-red);"
		></span>
		Backend unreachable — data may be stale
	</div>
{/if}
