/**
 * GET  /api/workspaces — proxy to FastAPI GET /workspaces
 * POST /api/workspaces — proxy to FastAPI POST /workspaces
 * Issue #106
 */
import { json } from '@sveltejs/kit';
import { apiFetch } from '$lib/api/client.js';
import type { RequestHandler } from './$types.js';

export const GET: RequestHandler = async () => {
	const result = await apiFetch<unknown[]>('/workspaces');
	if (!result.ok) {
		return json({ data: null, errors: result.errors }, { status: result.status });
	}
	return json({ data: result.data, errors: [] });
};

export const POST: RequestHandler = async ({ request }) => {
	let body: unknown;
	try {
		body = await request.json();
	} catch {
		return json({ data: null, errors: ['Invalid JSON body'] }, { status: 400 });
	}
	const result = await apiFetch<unknown[]>('/workspaces', {
		method: 'POST',
		body: JSON.stringify(body)
	});
	if (!result.ok) {
		return json({ data: null, errors: result.errors }, { status: result.status });
	}
	return json({ data: result.data, errors: [] }, { status: 201 });
};
