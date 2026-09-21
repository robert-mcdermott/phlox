"""Reviewed API contracts; transport, source access and file publication stay shared."""
from dataclasses import dataclass
from typing import Callable

from app.api_dataset_formats import nih_files, pubmed_files
from app.web_extract_worker import nih_projects, pubmed_records

NIH_NOTICE = ('These are retained parent-project records from one NIH RePORTER query, not verified NIH-only annual funding. '
              'Name fragments can match multiple organizations; agency scope and fiscal-year completeness require review. '
              'Known award sums exclude null amounts. Offset pagination is not a frozen database snapshot.')
PUBMED_NOTICE = ('Selected PubMed bibliographic records only: abstracts and full article text were not retrieved. '
                 'Do not cite titles as evidence of study findings. Query translation records how PubMed interpreted '
                 'the search. Pagination is not a frozen database snapshot or a systematic-review completeness guarantee.')


@dataclass(frozen=True)
class Adapter:
    name: str
    title: str
    method: str
    id_field: str
    validate_page: Callable
    notice: str
    dataset_files: Callable
    endpoint_setting: str
    ascending_ids: bool = False

    @property
    def endpoint(self):
        # Resolve configured constants at use time, also allowing isolated HTTP fixtures.
        from app import public_api
        return getattr(public_api, self.endpoint_setting)


ADAPTERS = {
    'nih_projects': Adapter('nih_projects', 'NIH RePORTER project query', 'POST', 'appl_id',
                            nih_projects, NIH_NOTICE, nih_files, 'ENDPOINT', ascending_ids=True),
    'pubmed': Adapter('pubmed', 'PubMed publication query', 'GET', 'pmid', pubmed_records,
                      PUBMED_NOTICE, pubmed_files, 'PUBMED_ENDPOINT'),
}


def get(name):
    if name not in ADAPTERS:
        raise ValueError('Unsupported public API adapter.')
    return ADAPTERS[name]
