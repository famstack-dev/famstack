"""`stack kettle boil`, as a family runs it: the kettle extension's own tests.

Written as any extension's tests are, and run the way they are, through the
extension lane:

    script/test extension tests/extensions/kettle

`stack` is the kettle installed and up in a Simpsons instance, and
`httpserver` stands in for the kettle's service.
"""


def test_boil_answers_the_reading_of_the_kettles_service(stack, httpserver):
    """Given the kettle's service reports 100 °C, when a person runs
    `stack kettle boil --json`, then the answer is that reading, boiling.

    The reading comes from the service through the bot runner, so this is
    the path every extension command with work in a container takes."""
    httpserver.expect_request("/water").respond_with_json({"celsius": 100})
    stack.serves("stack-kettle:8080", httpserver)

    ran = stack.run("kettle", "boil", "--json")

    assert ran.code == 0, ran
    assert ran.json == {"celsius": 100, "boiling": True}
