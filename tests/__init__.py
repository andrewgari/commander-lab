"""Test package setup (runs under both pytest and `unittest discover`).

Brew tests never call the real oracle: the oracle reviewer defaults to a
passing fake. The deterministic gate in brew_review still runs."""


def offline_reviewer(draft, session):
    return {"status": "pass", "reasons": [], "source": "test"}


def reset_reviewer():
    from src.server.domain.draft import brew_review

    brew_review.set_reviewer(offline_reviewer)


reset_reviewer()
