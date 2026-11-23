"""Filing a card into the family memory: the steps and the rules a writer follows for one card.

`stack.filing.file` files a capture: an intake (the archivist, `stack memory
capture`) builds a `stack.filing.capture.Capture` and a
`stack.filing.policy.Policy` and hands them over with its ports, the
reading and the writer. `stack.filing.cards` is the card a capture is
written as, `stack.filing.write` the one curator commit that writes it,
and `stack.filing.identity` how a note is found again by the messages it
was filed from. `stack.filing.refile` files a note again from every one
of its sources when someone corrects it.

`stack.filing.vocabulary` is the topics a card may carry at its place;
`stack.filing.topic_list` is the list of them a model chooses from, and
reading its choice back; `stack.filing.card_tags` puts a card's tags
together. `stack.filing.people` is the household's people a capture's card
names.
"""
