"""The water, as the kettle's service reports it, read as a reading.

A module of the package `stacklets.kettle`, which the bot runner's entry
point imports by that name.
"""

BOILING = 100  # °C at sea level, which is where the made-up kettle stands


def reading(answer: dict) -> dict:
    """`{"celsius": 97}` from the service as `{"celsius": 97, "boiling": False}`."""
    celsius = answer["celsius"]
    return {"celsius": celsius, "boiling": celsius >= BOILING}
