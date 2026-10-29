import { fail, redirect } from '@sveltejs/kit';
import type { Actions, PageServerLoad } from './$types';

// Fixture backend for the form-pending pattern (src/web/tests/form_pending.py).
// Every action echoes `source` (a hidden field, falling back to the action name) so each form on the
// page can show only its own result.

const wait = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

async function read(request: Request, fallback: string) {
	const data = await request.formData();
	return { title: String(data.get('title') ?? ''), source: String(data.get('source') ?? fallback) };
}

export const load: PageServerLoad = ({ url }) => ({ done: url.searchParams.get('done') === '1' });

export const actions: Actions = {
	ok: async ({ request }) => {
		const { title, source } = await read(request, 'ok');
		return { message: `Saved “${title}”`, source };
	},
	slow: async ({ request }) => {
		const { title, source } = await read(request, 'slow');
		await wait(1500);
		return { message: `Saved “${title}” slowly`, source };
	},
	invalid: async ({ request }) => {
		const { source } = await read(request, 'invalid');
		return fail(400, { actionError: 'That title is not valid.', source });
	},
	boom: async ({ request }) => {
		const { source } = await read(request, 'boom');
		return fail(503, { actionError: 'The backend is unavailable.', source });
	},
	throws: async () => {
		throw new Error('The action threw');
	},
	redirect: async () => {
		redirect(303, '/dev/components/form-pending?done=1');
	}
};
