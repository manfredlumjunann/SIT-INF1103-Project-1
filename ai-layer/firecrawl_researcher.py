# Finds case law and precedents for a flagged clause using Firecrawl.

import logging
from typing import Dict, List, Optional

from firecrawl import FirecrawlApp

logger = logging.getLogger('contract-analyzer')


class FirecrawlResearcher:
    RESULT_LIMIT = 3
    SUMMARY_LENGTH = 300

    def __init__(self, api_key: Optional[str]) -> None:
        self._api_key = api_key

    @property
    def is_configured(self) -> bool:
        return bool(self._api_key)

    # Up to three {title, url, summary} references for the clause, or [] when research is
    # not configured or fails (research is optional, so a failure never stops an analysis).
    def research(self, clause: Dict) -> List[Dict[str, str]]:
        if not self.is_configured:
            return []
        try:
            query = f"contract law \"{clause['clause_type']}\" court case precedent ruling outcome"
            results = FirecrawlApp(api_key=self._api_key).search(query=query, params={'limit': self.RESULT_LIMIT})
            references = []
            if results and results.get('data'):
                for item in results['data'][:self.RESULT_LIMIT]:
                    references.append({
                        'title': item.get('title', 'Unknown Case'),
                        'url': item.get('url', ''),
                        'summary': item.get('description', '')[:self.SUMMARY_LENGTH],
                    })
            return references
        except Exception as error:
            logger.warning("Firecrawl search failed: %s", error)
            return []
