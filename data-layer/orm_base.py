from sqlalchemy import MetaData
from sqlalchemy import Sequence as DbSequence
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    # Named constraints give readable database error messages (repositories look for 'uq_users_username')
    metadata = MetaData(naming_convention={
        'ix': 'ix_%(column_0_label)s',
        'uq': 'uq_%(table_name)s_%(column_0_name)s',
        'ck': 'ck_%(table_name)s_%(constraint_name)s',
        'fk': 'fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s',
        'pk': 'pk_%(table_name)s',
    })


# Numbers behind the readable ids (us_001, an_001); formatted by the repositories
USER_ID_SEQUENCE = DbSequence('users_id_seq', metadata=Base.metadata)
ANALYSIS_ID_SEQUENCE = DbSequence('analyses_id_seq', metadata=Base.metadata)
COMMENT_ID_SEQUENCE = DbSequence('comments_id_seq', metadata=Base.metadata)
