"""The parts of a topic's page: its compact head and the lists behind two chips.

    from memory_home import topic

    head = topic.head(kicker, title, facts, answer, context)
    chips = topic.chips(answer, context)
    main = topic.main_list(answer, context)        # "" unless links or documents fill the topic
    under = topic.panels(answer, context)

`topic_page.sheet` composes them. The head is the kicker, the title, the
facts and, when a model summarised an entry, the key that explains ✦. Two
chips ("3 bookmarks", "1 document") open the matching list under them, one
at a time (`static/topic.js`). A topic whose cards are mostly of one of the
two kinds opens on that list, which is then its main list: a section after
Up next, with the newest rows and "All n ›".

Pure: no I/O. The answer is the memory's overview of the topic.
"""

from memory_home.topic.chips import chips, main_kind, main_list, panels
from memory_home.topic.head import head

__all__ = ["head", "chips", "panels", "main_list", "main_kind"]
