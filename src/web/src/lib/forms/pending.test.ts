import { afterEach, describe, expect, it, vi } from 'vitest';
import { createPending, NETWORK_ERROR_MESSAGE } from './pending.svelte.ts';

type Result = { type: string; status?: number; [k: string]: unknown };

function fd(entries: Record<string, string> = {}): FormData {
	const f = new FormData();
	for (const [k, v] of Object.entries(entries)) f.set(k, v);
	return f;
}

/** Drive p.enhance the way SvelteKit does: call it, then call the returned callback. */
function submit(
	p: ReturnType<typeof createPending>,
	{ url = 'http://x/?/commit', formData = fd(), result = { type: 'success', status: 200 } as Result } = {}
) {
	const cancel = vi.fn();
	const update = vi.fn(async () => {});
	const ret = (p.enhance as unknown as (i: unknown) => unknown)({
		action: new URL(url),
		formData,
		formElement: document.createElement('form'),
		controller: new AbortController(),
		submitter: null,
		cancel
	});
	return {
		cancel,
		update,
		callback: ret as undefined | ((o: { update: typeof update; result: Result }) => Promise<void>),
		finish: () => (ret as (o: unknown) => Promise<void>)({ update, result })
	};
}

afterEach(() => vi.restoreAllMocks());

describe('createPending', () => {
	it('starts idle', () => {
		const p = createPending();
		expect(p.submitting).toBe(false);
		expect(p.action).toBeNull();
		expect(p.key).toBeNull();
		expect(p.transientError).toBeNull();
	});

	it('success: update({reset:true}) and the flag clears', async () => {
		const p = createPending();
		const s = submit(p);
		expect(s.cancel).not.toHaveBeenCalled();
		expect(p.submitting).toBe(true);
		await s.finish();
		expect(s.update).toHaveBeenCalledExactlyOnceWith({ reset: true });
		expect(p.submitting).toBe(false);
	});

	it('redirect: goes through update({reset:true})', async () => {
		const p = createPending();
		const s = submit(p, { result: { type: 'redirect', status: 303, location: '/x' } });
		await s.finish();
		expect(s.update).toHaveBeenCalledExactlyOnceWith({ reset: true });
		expect(p.submitting).toBe(false);
	});

	it('failure 400: update({reset:false}), no transient error', async () => {
		const p = createPending();
		const s = submit(p, { result: { type: 'failure', status: 400, data: {} } });
		await s.finish();
		expect(s.update).toHaveBeenCalledExactlyOnceWith({ reset: false });
		expect(p.transientError).toBeNull();
		expect(p.submitting).toBe(false);
	});

	it('failure 499 is still a validation-style failure', async () => {
		const p = createPending();
		const s = submit(p, { result: { type: 'failure', status: 499 } });
		await s.finish();
		expect(s.update).toHaveBeenCalledExactlyOnceWith({ reset: false });
	});

	it('failure 503: update not called, transientError set, flag clears', async () => {
		const p = createPending();
		const s = submit(p, { result: { type: 'failure', status: 503, data: {} } });
		await s.finish();
		expect(s.update).not.toHaveBeenCalled();
		expect(p.transientError).toBe(NETWORK_ERROR_MESSAGE);
		expect(p.transientError).toBe('Network or server error — your input was kept. Try again.');
		expect(p.submitting).toBe(false);
	});

	it("result.type 'error': update not called, transientError set, flag clears", async () => {
		const p = createPending();
		const s = submit(p, { result: { type: 'error', error: new Error('x') } });
		await s.finish();
		expect(s.update).not.toHaveBeenCalled();
		expect(p.transientError).toBe(NETWORK_ERROR_MESSAGE);
		expect(p.submitting).toBe(false);
	});

	it('a thrown error from update(): transientError set, flag clears, nothing escapes', async () => {
		const p = createPending();
		const s = submit(p);
		s.update.mockRejectedValueOnce(new Error('network down'));
		await expect(s.finish()).resolves.toBeUndefined();
		expect(s.update).toHaveBeenCalledTimes(1);
		expect(p.transientError).toBe(NETWORK_ERROR_MESSAGE);
		expect(p.submitting).toBe(false);
	});

	it('double submit: the second is cancelled and does not disturb the first', async () => {
		const p = createPending();
		const first = submit(p);
		const second = submit(p);
		expect(second.cancel).toHaveBeenCalledOnce();
		expect(second.callback).toBeUndefined();
		expect(first.cancel).not.toHaveBeenCalled();
		expect(p.submitting).toBe(true);
		await first.finish();
		expect(p.submitting).toBe(false);
		// the lock is released, so a later submit goes through
		const third = submit(p);
		expect(third.cancel).not.toHaveBeenCalled();
		await third.finish();
	});

	it('confirm returning false: cancel() and the form is NOT locked', () => {
		const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false);
		const p = createPending({ confirm: () => 'Sure?' });
		const s = submit(p);
		expect(confirmSpy).toHaveBeenCalledExactlyOnceWith('Sure?');
		expect(s.cancel).toHaveBeenCalledOnce();
		expect(p.submitting).toBe(false);
		expect(p.action).toBeNull();
		// not locked: the next submit (confirmed) is accepted
		confirmSpy.mockReturnValue(true);
		const again = submit(p);
		expect(again.cancel).not.toHaveBeenCalled();
		expect(p.submitting).toBe(true);
	});

	it('confirm returning a message and the user accepting: proceeds', async () => {
		vi.spyOn(window, 'confirm').mockReturnValue(true);
		const p = createPending({ confirm: () => 'Sure?' });
		const s = submit(p);
		expect(s.cancel).not.toHaveBeenCalled();
		expect(p.submitting).toBe(true);
		await s.finish();
	});

	it('confirm returning null: no prompt', async () => {
		const confirmSpy = vi.spyOn(window, 'confirm');
		const p = createPending({ confirm: () => null });
		const s = submit(p);
		expect(confirmSpy).not.toHaveBeenCalled();
		expect(s.cancel).not.toHaveBeenCalled();
		await s.finish();
	});

	it('confirm receives {formData, action} so one instance can hold different prompts', () => {
		const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true);
		const confirm = vi.fn(({ action }: { action: string }) => (action === 'abandon' ? 'Abandon?' : null));
		const p = createPending({ confirm });
		const data = fd({ id: '7' });
		submit(p, { url: 'http://x/?/commit', formData: data }).finish();
		expect(confirm).toHaveBeenLastCalledWith({ formData: data, action: 'commit' });
		expect(confirmSpy).not.toHaveBeenCalled();
	});

	it('a submit that arrives while locked is cancelled even when confirmed', async () => {
		vi.spyOn(window, 'confirm').mockReturnValue(true);
		const p = createPending({ confirm: () => 'Sure?' });
		const first = submit(p);
		const second = submit(p);
		expect(second.cancel).toHaveBeenCalledOnce();
		expect(first.cancel).not.toHaveBeenCalled();
		await first.finish();
	});

	it('exposes action and key in flight and clears them after', async () => {
		const p = createPending({ key: (f: FormData) => String(f.get('oracle_id')) });
		const s = submit(p, { url: 'http://x/drafts/1?/remove_card', formData: fd({ oracle_id: 'abc' }) });
		expect(p.action).toBe('remove_card');
		expect(p.key).toBe('abc');
		await s.finish();
		expect(p.action).toBeNull();
		expect(p.key).toBeNull();
	});

	it("action is 'default' when the form has no named action", async () => {
		const p = createPending();
		const s = submit(p, { url: 'http://x/drafts' });
		expect(p.action).toBe('default');
		await s.finish();
	});

	it('key stays null without opts.key', async () => {
		const p = createPending();
		const s = submit(p);
		expect(p.key).toBeNull();
		await s.finish();
	});

	it('transientError is cleared on the next submit and by clearError()', async () => {
		const p = createPending();
		await submit(p, { result: { type: 'error', error: 1 } }).finish();
		expect(p.transientError).not.toBeNull();
		p.clearError();
		expect(p.transientError).toBeNull();

		await submit(p, { result: { type: 'error', error: 1 } }).finish();
		expect(p.transientError).not.toBeNull();
		const next = submit(p);
		expect(p.transientError).toBeNull();
		await next.finish();
	});
});
