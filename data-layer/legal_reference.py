import hashlib
from urllib.parse import urlparse

from sqlalchemy import CheckConstraint, Text
from sqlalchemy.orm import Mapped, mapped_column

from orm_base import Base
from entity_helpers import in_list
from verification import Verification


class LegalReference(Base):
    __tablename__ = 'legal_references'
    __table_args__ = (
        CheckConstraint(in_list('verification', Verification), name='verification'),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    url: Mapped[str] = mapped_column(Text, unique=True)
    title: Mapped[str] = mapped_column(Text)
    domain: Mapped[str] = mapped_column(Text)
    _verification: Mapped[str] = mapped_column('verification', Text,
                                               server_default=Verification.UNVERIFIED.value)

    def __init__(self, id: str, url: str, title: str, domain: str,
                 verification: Verification = Verification.UNVERIFIED) -> None:
        self.id = id
        self.url = url
        self.title = title
        self.domain = domain
        self._verification = Verification(verification).value

    # A new, unverified reference. The id comes from the URL, so the same source is stored once.
    @classmethod
    def from_url(cls, url: str, title: str = '') -> 'LegalReference':
        host = urlparse(url).netloc.lower()
        return cls(id='ref_' + hashlib.sha256(url.encode('utf-8')).hexdigest()[:12],
                   url=url, title=title or url,
                   domain=host[4:] if host.startswith('www.') else host)

    @property
    def verification(self) -> Verification:
        return Verification(self._verification)

    # Record whether the source has been checked; unknown states are refused.
    @verification.setter
    def verification(self, value) -> None:
        self._verification = Verification.parse(value).value
