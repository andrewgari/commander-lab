// Pure: map a failed commit/abandon response to dialog copy (page-specs §2.6 "Failure").

export interface CommitFailure {
	message: string;
	/** A 4xx other than validation means the world changed under us: offer "Close and refresh". */
	stale: boolean;
}

export function describeCommitFailure(status: number | undefined, reason: string | undefined): CommitFailure {
	const message = (reason ?? '').trim() || 'The server rejected the commit.';
	return { message, stale: status === 409 || status === 400 || status === 404 };
}
