"""Reviewed API contracts; transport, source access and file publication stay shared."""
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urlencode

from app.api_dataset_formats import nih_files, pubmed_files, trial_files
from app.web_extract_worker import nih_projects, pubmed_records, trial_records

NIH_NOTICE = ('These are retained parent-project records from one NIH RePORTER query, not verified NIH-only annual funding. '
              'Name fragments can match multiple organizations; agency scope and fiscal-year completeness require review. '
              'Known award sums exclude null amounts. Offset pagination is not a frozen database snapshot.')
PUBMED_NOTICE = ('Selected PubMed bibliographic records only: abstracts and full article text were not retrieved. '
                 'Do not cite titles as evidence of study findings. Query translation records how PubMed interpreted '
                 'the search. Pagination is not a frozen database snapshot or a systematic-review completeness guarantee.')
TRIAL_NOTICE = ('ClinicalTrials.gov registry records, not independently verified findings or medical advice. '
                'Overall recruitment status can differ from site status; has_results describes posted results, not recruitment. '
                'Keyword/sponsor/location searches are discovery matches, not proof of institutional involvement. '
                'Verify that connection in sponsor/site details. Missing fields are unknown. '
                'Last update posted is the registry date, not retrieval time. The match count comes from the first page; '
                'later pages do not refresh it. Cursor pagination is not a frozen snapshot.')
TRIAL_DETAIL_NOTICE = (TRIAL_NOTICE + ' Study sections are JSON text passages; partial passages can omit groups, units or context. '
                       'Read the complete relevant evidence before comparing outcomes; no posted results is not proof that a study failed. '
                       'Use overview for sponsors and locations for sites. Selections do not represent the complete study record.')


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
    detail_endpoint_setting: str | None = None
    detail_format: str | None = None
    detail_parameters: Callable | None = None
    detail_response_format: str = 'xml'
    detail_path: bool = False
    detail_notice: str | None = None

    def record_endpoint(self, identifier):
        return self.detail_endpoint + '/' + identifier if self.detail_path else self.detail_endpoint

    def record_url(self, identifier):
        endpoint = self.record_endpoint(identifier)
        return endpoint + '?' + urlencode(self.detail_parameters(identifier)) if self.detail_parameters else endpoint

    @property
    def detail_endpoint(self):
        from app import public_api
        return getattr(public_api, self.detail_endpoint_setting) if self.detail_endpoint_setting else None

    @property
    def endpoint(self):
        # Resolve configured constants at use time, also allowing isolated HTTP fixtures.
        from app import public_api
        return getattr(public_api, self.endpoint_setting)


ADAPTERS = {
    'nih_projects': Adapter('nih_projects', 'NIH RePORTER project query', 'POST', 'appl_id',
                            nih_projects, NIH_NOTICE, nih_files, 'ENDPOINT', ascending_ids=True),
    'pubmed': Adapter('pubmed', 'PubMed publication query', 'GET', 'pmid', pubmed_records,
                      PUBMED_NOTICE, pubmed_files, 'PUBMED_ENDPOINT',
                      detail_endpoint_setting='PUBMED_DETAIL_ENDPOINT', detail_format='pubmed_detail',
                      detail_parameters=lambda identifier: {'db': 'pubmed', 'id': identifier, 'retmode': 'xml', 'tool': 'phlox'}),
    'clinical_trials': Adapter('clinical_trials', 'ClinicalTrials.gov study query', 'GET', 'nct_id',
                              trial_records, TRIAL_NOTICE, trial_files, 'CLINICAL_TRIALS_ENDPOINT',
                              detail_endpoint_setting='CLINICAL_TRIALS_ENDPOINT', detail_format='clinical_trials_detail',
                              detail_response_format='json', detail_path=True, detail_notice=TRIAL_DETAIL_NOTICE),
}


def get(name):
    if name not in ADAPTERS:
        raise ValueError('Unsupported public API adapter.')
    return ADAPTERS[name]
