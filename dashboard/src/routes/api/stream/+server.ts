/**
 * GET /api/stream — SSE passthrough to FastAPI GET /stream
 *
 * Pipes the backend SSE stream through to the browser so the
 * BACKEND_URL and API_SECRET never reach the client.
 *
 * Issue #37
 */
import { apiStream } from '$lib/api/client.js';
import type { RequestHandler } from './$types.js';

export const GET: RequestHandler = async () => {
	try {
		const upstream = await apiStream();

		if (!upstream.ok || !upstream.body) {
			return new Response('Backend SSE stream unavailable', {
				status: upstream.status || 502
			});
		}

		// Pipe the upstream SSE body straight through
		return new Response(upstream.body, {
			headers: {
				'Content-Type': 'text/event-stream',
				'Cache-Control': 'no-cache',
				Connection: 'keep-alive'
			}
		});
	} catch {
		return new Response('Backend unreachable', { status: 502 });
	}
};
