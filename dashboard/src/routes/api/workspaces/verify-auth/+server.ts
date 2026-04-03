/**
 * POST /api/workspaces/verify-auth — proxy to FastAPI POST /workspaces/verify-auth
 * Issue #106
 */
import { json } from '@sveltejs/kit';
import { apiFetch } from '$lib/api/client.js';
import type { RequestHandler } from './$types.js';

export const POST: RequestHandler = async ({ request }) => {
	let body: unknown;
	try {
		body = await request.json();
	} catch {
		return json({ data: null, errors: ['Invalid JSON body'] }, { status: 400 });
	}
	const result = await apiFetch<unknown>('/workspaces/verify-auth', {
		method: 'POST',
		body: JSON.stringify(body)
	});
	if (!result.ok) {
		return json({ data: null, errors: result.errors }, { status: result.status });
	}
	return json({ data: result.data, errors: [] });
};
