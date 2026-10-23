"""Local synthetic fixture SOURCE ONLY; not created/executed in Dirty Mode."""
import pytest

from rfp_matrix.evidence import build_matrix, make_corpus
from rfp_matrix.review import ReviewStore
from rfp_matrix.review.application import ReviewerApplication
from rfp_matrix.source import SourceRegistry, parse_text_pages


@pytest.fixture
def application(tmp_path):
    store = ReviewStore(tmp_path / "state")
    corpus = make_corpus([])
    for run_id in ("R1", "R2"):
        artifact = parse_text_pages(["The vendor must provide encrypted daily backups.", ""],
            source_id="synthetic-web-" + run_id, terms="Self-authored fictional development fixture")
        SourceRegistry(store.root / "sources").save(artifact)
        store.create_run(run_id, artifact, corpus, build_matrix(artifact, corpus)["rows"])
    return ReviewerApplication(store)
