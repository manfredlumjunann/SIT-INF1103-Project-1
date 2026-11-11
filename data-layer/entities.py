"""Importing this module registers every table with Base.metadata, so create_all() sees all of them.
Each entity lives in its own file; this is the one place that lists them."""

from analysis import Analysis
from clause import Clause
from clause_comment import ClauseComment
from clause_reference import ClauseReference
from legal_reference import LegalReference
from user import User

__all__ = ['Analysis', 'Clause', 'ClauseComment', 'ClauseReference', 'LegalReference', 'User']
