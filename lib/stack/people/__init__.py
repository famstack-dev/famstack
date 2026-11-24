"""The people the memory knows: declared person files and the names read from them.

`stack.people.files` reads and writes the declared person files (`people/<id>.md`).
`stack.people.names` reads the names that the classifier, the diary and the transcription use.
Both modules are re-exported here, so `from stack.people import Person` works.
"""

from .files import *  # noqa: F401,F403
from .files import (  # noqa: F401
    PEOPLE_DIR,
    Person,
    PersonError,
    Report,
    find_people,
    load_people,
    members,
    people_dir,
    people_from_accounts,
    person_id,
    person_path,
    read_person,
    render_person,
    resolve,
)
