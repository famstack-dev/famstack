"""What the stack can learn about an AI server before using it.

A dedicated speech server has no model list, so whether it transcribes
is asked of the route itself, before the first voice message fails.
"""

from stack.ai.probe import transcribes


class TestTranscription:

    def test_a_server_that_wants_the_missing_file_transcribes(self, httpserver):
        httpserver.expect_request("/v1/audio/transcriptions", method="POST") \
            .respond_with_data("file is required", status=422)

        assert transcribes(httpserver.url_for("/v1"))

    def test_a_server_without_the_route_does_not(self, httpserver):
        assert not transcribes(httpserver.url_for("/v1"))

    def test_nothing_listening_does_not(self):
        assert not transcribes("http://127.0.0.1:9/v1")

