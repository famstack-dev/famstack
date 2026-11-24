"""stack.filing.correction: how a filed card is corrected.

A person corrects a card with a reply in the chat. The model then reads
the card conversation and answers what the corrections change. This
package holds that logic without chat or I/O, because the archivist bot
and the commands share it.

| Module | What it holds |
|---|---|
| `conversation` | the card conversation that the model reads |
| `changes` | the card's fields after a correction, with one guard for tags and people |
| `note` | a corrected note's reading, from the model's answer |
| `document` | a document's correction: the conversation and the classification after it |
| `chain` | what a correction thread holds |
"""
