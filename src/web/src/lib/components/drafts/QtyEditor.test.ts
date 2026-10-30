import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/svelte';

type Cb = (o: { result: unknown; update: () => Promise<void> }) => Promise<void>;
type Submit = (input: unknown) => Promise<Cb | undefined> | Cb | undefined;
const hoisted = vi.hoisted(() => ({ submit: null as unknown }));

vi.mock('$app/forms', () => ({
	enhance: (_form: HTMLFormElement, fn: unknown) => {
		hoisted.submit = fn;
		return { destroy() {} };
	}
}));

import QtyEditor from './QtyEditor.svelte';

afterEach(() => {
	hoisted.submit = null;
});

function setup(onsaved = vi.fn()) {
	render(QtyEditor, { oracleId: 'o1', board: 'main', cardName: 'Sol Ring', boardLabel: 'Main', qty: 2, onsaved });
	const input = screen.getByLabelText('Quantity of Sol Ring on Main') as HTMLInputElement;
	const form = document.querySelector('form') as HTMLFormElement;
	const save = screen.getByRole('button', { name: 'Save quantity for Sol Ring on Main' });
	return { input, form, save, onsaved };
}

async function run() {
	const cancel = vi.fn();
	const cb = await (hoisted.submit as Submit)({
		action: new URL('http://x/drafts/1?/upsert_card'),
		formData: new FormData(),
		formElement: document.querySelector('form'),
		controller: new AbortController(),
		submitter: null,
		cancel
	});
	return { cancel, cb };
}

describe('QtyEditor', () => {
	it('posts to ?/upsert_card with hidden identity fields', () => {
		const { form } = setup();
		expect(form.getAttribute('action')).toBe('?/upsert_card');
		expect(form.method).toBe('post');
		expect((form.querySelector('[name=oracle_id]') as HTMLInputElement).value).toBe('o1');
		expect((form.querySelector('[name=board]') as HTMLInputElement).value).toBe('main');
	});

	it('hides Save (keeping its space) when pristine and shows it when dirty', async () => {
		const { form, input } = setup();
		expect(form.classList.contains('is-dirty')).toBe(false);
		await fireEvent.input(input, { target: { value: '4' } });
		expect(form.classList.contains('is-dirty')).toBe(true);
		await fireEvent.input(input, { target: { value: '2' } });
		expect(form.classList.contains('is-dirty')).toBe(false);
	});

	it('submits the exact qty and reports Saved {name}: {qty}', async () => {
		const { input, onsaved } = setup();
		await fireEvent.input(input, { target: { value: '7' } });
		expect(input.value).toBe('7');
		const { cancel, cb } = await run();
		expect(cancel).not.toHaveBeenCalled();
		await cb!({ result: { type: 'success' }, update: async () => {} });
		expect(onsaved).toHaveBeenCalledWith('Saved Sol Ring: 7');
	});

	it('does not report when the server rejects', async () => {
		const { input, onsaved } = setup();
		await fireEvent.input(input, { target: { value: '3' } });
		const { cb } = await run();
		await cb!({ result: { type: 'failure', status: 400 }, update: async () => {} });
		expect(onsaved).not.toHaveBeenCalled();
	});

	it.each(['0', '-1', '1.5', ''])('rejects %j without submitting', async (v) => {
		const { input } = setup();
		await fireEvent.input(input, { target: { value: v } });
		const { cancel, cb } = await run();
		expect(cancel).toHaveBeenCalled();
		expect(cb).toBeUndefined();
	});

	it('does not submit when pristine', async () => {
		setup();
		const { cancel } = await run();
		expect(cancel).toHaveBeenCalled();
	});
});
