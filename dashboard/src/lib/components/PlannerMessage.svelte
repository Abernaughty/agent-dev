<!--
	PlannerMessage — renders a single chat message with role-aware formatting.

	For Planner messages: lightweight markdown subset renderer that handles
	**bold**, `inline code`, bullet lists, numbered lists, and paragraph
	spacing. Includes an amber "P" avatar badge.

	For system messages: renders with markdown formatting (bold, code, etc.)
	for structured welcome messages.

	For other roles (user, event): renders as plain text.

	No external dependencies — uses regex-based parsing on a safe subset.
	All output is built programmatically (no raw innerHTML from user content).
-->
<script lang="ts">
	import type { PlannerChatMessage } from '$lib/stores/planner.svelte.js';

	interface Props {
		msg: PlannerChatMessage;
	}

	let { msg }: Props = $props();

	// -- Inline markdown parsing --
	// Produces an array of segments: { type, text } for rendering.

	type InlineSegment =
		| { type: 'text'; text: string }
		| { type: 'bold'; text: string }
		| { type: 'code'; text: string };

	function parseInline(line: string): InlineSegment[] {
		const segments: InlineSegment[] = [];
		// Match **bold** and `code` patterns
		const pattern = /\*\*(.+?)\*\*|`([^`]+)`/g;
		let lastIndex = 0;
		let match: RegExpExecArray | null;

		while ((match = pattern.exec(line)) !== null) {
			// Text before this match
			if (match.index > lastIndex) {
				segments.push({ type: 'text', text: line.slice(lastIndex, match.index) });
			}
			if (match[1] !== undefined) {
				segments.push({ type: 'bold', text: match[1] });
			} else if (match[2] !== undefined) {
				segments.push({ type: 'code', text: match[2] });
			}
			lastIndex = match.index + match[0].length;
		}

		// Remaining text
		if (lastIndex < line.length) {
			segments.push({ type: 'text', text: line.slice(lastIndex) });
		}

		return segments.length > 0 ? segments : [{ type: 'text', text: line }];
	}

	// -- Block-level parsing --
	// Converts text lines into structured blocks for rendering.

	type Block =
		| { type: 'paragraph'; segments: InlineSegment[] }
		| { type: 'bullet'; segments: InlineSegment[] }
		| { type: 'numbered'; number: string; segments: InlineSegment[] }
		| { type: 'break' };

	function parseBlocks(text: string): Block[] {
		const lines = text.split('\n');
		const blocks: Block[] = [];

		for (const rawLine of lines) {
			const line = rawLine;

			// Blank line → paragraph break
			if (line.trim() === '') {
				// Avoid consecutive breaks
				if (blocks.length > 0 && blocks[blocks.length - 1].type !== 'break') {
					blocks.push({ type: 'break' });
				}
				continue;
			}

			// Bullet list: "- item" or "* item"
			const bulletMatch = line.match(/^\s*[-*]\s+(.+)$/);
			if (bulletMatch) {
				blocks.push({ type: 'bullet', segments: parseInline(bulletMatch[1]) });
				continue;
			}

			// Numbered list: "1. item" or "1) item"
			const numberedMatch = line.match(/^\s*(\d+)[.)\]]\s+(.+)$/);
			if (numberedMatch) {
				blocks.push({
					type: 'numbered',
					number: numberedMatch[1],
					segments: parseInline(numberedMatch[2]),
				});
				continue;
			}

			// Regular paragraph line
			blocks.push({ type: 'paragraph', segments: parseInline(line) });
		}

		// Trim trailing break
		if (blocks.length > 0 && blocks[blocks.length - 1].type === 'break') {
			blocks.pop();
		}

		return blocks;
	}

	// -- Compute blocks for the current message --
	const useMarkdown = $derived(msg.role === 'planner' || msg.role === 'system');
	const blocks = $derived(useMarkdown ? parseBlocks(msg.text) : []);
</script>

{#if useMarkdown}
	<div class="planner-md" style="overflow-wrap: break-word;">
		{#each blocks as block}
			{#if block.type === 'break'}
				<div style="height: 8px;"></div>

			{:else if block.type === 'bullet'}
				<div class="flex gap-2" style="padding-left: 4px; margin-bottom: 3px;">
					<span style="color: var(--color-text-dim); flex-shrink: 0; user-select: none;">•</span>
					<span style="line-height: 1.6;">
						{#each block.segments as seg}
							{#if seg.type === 'bold'}
								<strong style="color: var(--color-text-bright); font-weight: 600;">{seg.text}</strong>
							{:else if seg.type === 'code'}
								<code
									style="
										background: var(--color-accent-cyan)08;
										color: var(--color-accent-cyan);
										padding: 1px 5px;
										border-radius: 3px;
										font-size: 0.92em;
									"
								>{seg.text}</code>
							{:else}
								{seg.text}
							{/if}
						{/each}
					</span>
				</div>

			{:else if block.type === 'numbered'}
				<div class="flex gap-2" style="padding-left: 4px; margin-bottom: 3px;">
					<span
						style="
							color: var(--color-accent-amber);
							flex-shrink: 0;
							min-width: 16px;
							text-align: right;
							font-weight: 600;
							user-select: none;
						"
					>{block.number}.</span>
					<span style="line-height: 1.6;">
						{#each block.segments as seg}
							{#if seg.type === 'bold'}
								<strong style="color: var(--color-text-bright); font-weight: 600;">{seg.text}</strong>
							{:else if seg.type === 'code'}
								<code
									style="
										background: var(--color-accent-cyan)08;
										color: var(--color-accent-cyan);
										padding: 1px 5px;
										border-radius: 3px;
										font-size: 0.92em;
									"
								>{seg.text}</code>
							{:else}
								{seg.text}
							{/if}
						{/each}
					</span>
				</div>

			{:else}
				<!-- paragraph -->
				<p style="margin-bottom: 2px; line-height: 1.6;">
					{#each block.segments as seg}
						{#if seg.type === 'bold'}
							<strong style="color: var(--color-text-bright); font-weight: 600;">{seg.text}</strong>
						{:else if seg.type === 'code'}
							<code
								style="
									background: var(--color-accent-cyan)08;
									color: var(--color-accent-cyan);
									padding: 1px 5px;
									border-radius: 3px;
									font-size: 0.92em;
								"
							>{seg.text}</code>
						{:else}
							{seg.text}
						{/if}
					{/each}
				</p>
			{/if}
		{/each}
	</div>
{:else}
	<!-- Non-markdown roles: render as plain text -->
	<span style="overflow-wrap: break-word;">{msg.text}</span>
{/if}
